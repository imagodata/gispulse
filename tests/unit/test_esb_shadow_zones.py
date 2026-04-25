"""
Tests unitaires — Zones d'ombre ESB (shadow zones).

Couvre les fichiers sans aucun test :
- adapters/esb/workers/dispatch_worker.py
- adapters/esb/workers/identify_worker.py
- adapters/esb/bus_message.py (serialisation / deserialisation / helpers)
- adapters/esb/pg_notify.py (PgNotifyChannel + PgNotifyListener)

Aucune connexion reelle a PostgreSQL. Tout est mocke.

Beta : "Hé, j'ai remarqué que ces 4 fichiers n'avaient AUCUN test.
       Zero. Nada. J'ai fouillé partout. Alors j'ai écrit ceux-là."
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

try:
    import asyncpg as _asyncpg  # noqa: F401
    _HAS_ASYNCPG = True
except ImportError:
    _HAS_ASYNCPG = False

from gispulse.adapters.esb.bus_message import BusMessage
from gispulse.adapters.esb.enums import MessageStatus, WorkerType
from gispulse.adapters.esb.pg_notify import PgNotifyChannel, PgNotifyListener
from gispulse.adapters.esb.workers.dispatch_worker import DispatchWorker
from gispulse.adapters.esb.workers.identify_worker import IdentifyWorker


# ---------------------------------------------------------------------------
# Helpers — mock asyncpg pool / connection
# ---------------------------------------------------------------------------


def make_mock_conn(**overrides) -> AsyncMock:
    """Crée un mock de connexion asyncpg."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.add_listener = AsyncMock()
    conn.remove_listener = AsyncMock()
    conn.close = AsyncMock()
    for k, v in overrides.items():
        setattr(conn, k, v)
    return conn


def make_mock_pool(conn: AsyncMock | None = None) -> MagicMock:
    """Crée un mock de pool asyncpg avec acquire() comme context manager."""
    if conn is None:
        conn = make_mock_conn()
    pool = MagicMock()

    @asynccontextmanager
    async def fake_acquire():
        yield conn

    pool.acquire = fake_acquire
    return pool


def make_message(
    operation: str = "UPDATE",
    data_category: str = "vector",
    **extra_payload: Any,
) -> BusMessage:
    """Crée un BusMessage minimal pour les tests."""
    payload = {
        "operation": operation,
        "data_category": data_category,
        "dataset_id": str(uuid4()),
        "layer_id": str(uuid4()),
        **extra_payload,
    }
    return BusMessage(
        id=uuid4(),
        channel_id=uuid4(),
        payload=payload,
        message_status=MessageStatus.NEW,
    )


# ===========================================================================
# BusMessage — sérialisation, désérialisation, helpers
# ===========================================================================


class TestBusMessageToDict:
    """to_dict() doit produire un dict sérialisable JSON."""

    def test_to_dict_contains_required_keys(self):
        msg = make_message()
        d = msg.to_dict()
        required = {
            "id", "channel_id", "message_status", "operation",
            "dataset_id", "layer_id", "data_category", "priority",
            "retry_count", "created_at", "dispatched",
        }
        assert required.issubset(d.keys())

    def test_to_dict_serializable(self):
        """Le dict produit doit être sérialisable en JSON sans erreur."""
        msg = make_message()
        serialized = json.dumps(msg.to_dict())
        assert isinstance(serialized, str)

    def test_to_dict_status_is_string(self):
        msg = make_message()
        d = msg.to_dict()
        assert isinstance(d["message_status"], str)
        assert d["message_status"] == "NEW"

    def test_to_dict_uuids_are_strings(self):
        msg = make_message()
        d = msg.to_dict()
        # Doit être un string, pas un UUID
        assert isinstance(d["id"], str)
        assert isinstance(d["channel_id"], str)

    def test_to_dict_with_no_dataset_id(self):
        """Et si le payload n'a pas de dataset_id ?"""
        msg = BusMessage(
            id=uuid4(),
            channel_id=uuid4(),
            payload={"operation": "DELETE"},
            message_status=MessageStatus.NEW,
        )
        d = msg.to_dict()
        assert d["dataset_id"] is None

    def test_to_dict_with_no_layer_id(self):
        msg = BusMessage(
            id=uuid4(),
            channel_id=uuid4(),
            payload={"operation": "INSERT"},
            message_status=MessageStatus.NEW,
        )
        d = msg.to_dict()
        assert d["layer_id"] is None


class TestBusMessageFromDbRow:
    """from_db_row() — construction depuis un dict imitant une ligne asyncpg."""

    def test_basic_from_db_row(self):
        row = {
            "id": uuid4(),
            "channel_id": uuid4(),
            "payload": {"operation": "INSERT", "data_category": "vector"},
            "message_status": "NEW",
        }
        msg = BusMessage.from_db_row(row)
        assert msg.id == row["id"]
        assert msg.message_status == MessageStatus.NEW
        assert msg.operation == "INSERT"

    def test_from_db_row_json_string_payload(self):
        """Et si le payload arrive en string JSON au lieu de dict ?"""
        payload_dict = {"operation": "UPDATE", "data_category": "raster"}
        row = {
            "id": uuid4(),
            "channel_id": uuid4(),
            "payload": json.dumps(payload_dict),
            "message_status": "PROCESSED",
        }
        msg = BusMessage.from_db_row(row)
        assert msg.payload == payload_dict
        assert msg.data_category == "raster"

    def test_from_db_row_missing_optional_fields(self):
        """Les champs optionnels doivent avoir des valeurs par défaut."""
        row = {
            "id": uuid4(),
            "channel_id": uuid4(),
            "payload": {},
            "message_status": "NEW",
        }
        msg = BusMessage.from_db_row(row)
        assert msg.message_priority == 5
        assert msg.retry_count == 0
        assert msg.dispatched is False
        assert msg.type_message_id is None

    def test_from_db_row_empty_payload(self):
        """Payload vide — ca doit pas planter."""
        row = {
            "id": uuid4(),
            "channel_id": uuid4(),
            "payload": {},
            "message_status": "NEW",
        }
        msg = BusMessage.from_db_row(row)
        assert msg.payload == {}
        assert msg.operation is None
        assert msg.data_category == "vector"  # valeur par défaut


class TestBusMessageAgeSeconds:
    """age_seconds() — j'ai essayé un truc : et si le message a 0 seconde ?"""

    def test_age_is_positive(self):
        msg = make_message()
        # Un message fraîchement créé doit avoir un âge >= 0
        assert msg.age_seconds() >= 0

    def test_age_of_old_message(self):
        msg = make_message()
        msg.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
        age = msg.age_seconds()
        # Au moins 3599 secondes (1h - marge)
        assert age >= 3599

    def test_processing_duration_none_when_incomplete(self):
        msg = make_message()
        assert msg.processing_duration_seconds() is None

    def test_processing_duration_computed(self):
        msg = make_message()
        msg.started_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        msg.completed_at = datetime(2026, 1, 1, 12, 0, 10, tzinfo=timezone.utc)
        assert msg.processing_duration_seconds() == 10.0


class TestBusMessagePayloadAccessors:
    """Les propriétés de payload — et si tout est vide, null, bizarre ?"""

    def test_dataset_id_as_uuid(self):
        uid = uuid4()
        msg = BusMessage(
            id=uuid4(), channel_id=uuid4(),
            payload={"dataset_id": uid},
            message_status=MessageStatus.NEW,
        )
        assert msg.dataset_id == uid

    def test_dataset_id_as_string(self):
        uid = uuid4()
        msg = BusMessage(
            id=uuid4(), channel_id=uuid4(),
            payload={"dataset_id": str(uid)},
            message_status=MessageStatus.NEW,
        )
        assert msg.dataset_id == uid

    def test_dataset_id_none(self):
        msg = BusMessage(
            id=uuid4(), channel_id=uuid4(),
            payload={},
            message_status=MessageStatus.NEW,
        )
        assert msg.dataset_id is None

    def test_layer_id_none(self):
        msg = BusMessage(
            id=uuid4(), channel_id=uuid4(),
            payload={},
            message_status=MessageStatus.NEW,
        )
        assert msg.layer_id is None

    def test_operation_helpers(self):
        for op, method in [
            ("INSERT", "is_insert"),
            ("UPDATE", "is_update"),
            ("DELETE", "is_delete"),
            ("PROCESS", "is_process"),
        ]:
            msg = make_message(operation=op)
            assert getattr(msg, method)() is True

    def test_trigger_timestamp_from_iso_string(self):
        msg = BusMessage(
            id=uuid4(), channel_id=uuid4(),
            payload={"trigger_timestamp": "2026-03-26T12:00:00+00:00"},
            message_status=MessageStatus.NEW,
        )
        ts = msg.trigger_timestamp
        assert isinstance(ts, datetime)
        assert ts.year == 2026

    def test_trigger_timestamp_with_z_suffix(self):
        """Et si le timestamp finit par Z au lieu de +00:00 ?"""
        msg = BusMessage(
            id=uuid4(), channel_id=uuid4(),
            payload={"trigger_timestamp": "2026-03-26T12:00:00Z"},
            message_status=MessageStatus.NEW,
        )
        ts = msg.trigger_timestamp
        assert isinstance(ts, datetime)

    def test_trigger_timestamp_none(self):
        msg = make_message()
        # Pas de trigger_timestamp dans le payload par défaut
        if "trigger_timestamp" not in msg.payload:
            assert msg.trigger_timestamp is None

    def test_processing_mode_default(self):
        msg = BusMessage(
            id=uuid4(), channel_id=uuid4(),
            payload={},
            message_status=MessageStatus.NEW,
        )
        assert msg.processing_mode == "SYNC"

    def test_affected_layers_empty_by_default(self):
        msg = make_message()
        assert msg.affected_layers == []

    def test_set_worker_result(self):
        msg = make_message()
        result = {"affected_layers": ["layer_a", "layer_b"], "count": 42}
        msg.set_worker_result(result)
        assert msg.affected_layers == ["layer_a", "layer_b"]
        assert msg.payload["_worker_result"] == result

    def test_new_data_and_old_data(self):
        msg = BusMessage(
            id=uuid4(), channel_id=uuid4(),
            payload={
                "new_data": {"geom": "POINT(0 0)"},
                "old_data": {"geom": "POINT(1 1)"},
            },
            message_status=MessageStatus.NEW,
        )
        assert msg.new_data == {"geom": "POINT(0 0)"}
        assert msg.old_data == {"geom": "POINT(1 1)"}


# ===========================================================================
# IdentifyWorker
# ===========================================================================


class TestIdentifyWorker:
    """
    Beta : "Le IdentifyWorker lit les messages NEW et les identifie.
           J'ai testé : que se passe-t-il avec 0 messages ? avec un payload
           en string ? avec un type inconnu ?"
    """

    @pytest.mark.asyncio
    async def test_run_batch_no_pool_returns_zero(self):
        """Sans pool, run_batch doit retourner 0 sans planter."""
        worker = IdentifyWorker(db_pool=None)
        result = await worker.run_batch()
        assert result == 0

    @pytest.mark.asyncio
    async def test_run_batch_empty_queue(self):
        """File vide — aucun message NEW en base."""
        conn = make_mock_conn()
        conn.fetch = AsyncMock(return_value=[])

        # La transaction est un context manager aussi
        tx = AsyncMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=tx)

        pool = make_mock_pool(conn)
        worker = IdentifyWorker(db_pool=pool)
        result = await worker.run_batch()
        assert result == 0

    @pytest.mark.asyncio
    async def test_run_batch_processes_one_message(self):
        """Un message NEW avec un type connu doit passer en IDENTIFIED."""
        msg_id = uuid4()
        type_id = uuid4()
        payload = json.dumps({"data_category": "vector", "operation": "INSERT"})

        conn = make_mock_conn()
        conn.fetch = AsyncMock(return_value=[
            {"id": msg_id, "payload": payload},
        ])
        conn.fetchrow = AsyncMock(return_value={"id": type_id})

        tx = AsyncMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=tx)

        pool = make_mock_pool(conn)
        worker = IdentifyWorker(db_pool=pool, batch_size=10)
        result = await worker.run_batch()

        assert result == 1
        # Vérifie que execute a été appelé (IDENTIFYING + IDENTIFIED)
        assert conn.execute.call_count >= 2

    @pytest.mark.asyncio
    async def test_run_batch_unknown_type(self):
        """Type inconnu — le message passe quand même en IDENTIFIED (mode souple)."""
        msg_id = uuid4()
        payload = {"data_category": "exotic", "operation": "TELEPORT"}

        conn = make_mock_conn()
        conn.fetch = AsyncMock(return_value=[
            {"id": msg_id, "payload": payload},
        ])
        conn.fetchrow = AsyncMock(return_value=None)  # Type inconnu

        tx = AsyncMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=tx)

        pool = make_mock_pool(conn)
        worker = IdentifyWorker(db_pool=pool)
        result = await worker.run_batch()

        assert result == 1
        # Le dernier execute doit contenir IDENTIFIED
        last_call_args = conn.execute.call_args_list[-1]
        assert MessageStatus.IDENTIFIED.value in last_call_args.args

    @pytest.mark.asyncio
    async def test_identify_payload_string_vs_dict(self):
        """Et si le payload est un dict au lieu d'un string JSON ?"""
        msg_id = uuid4()
        # Payload déjà en dict (pas un string)
        payload_dict = {"data_category": "vector", "operation": "UPDATE"}

        conn = make_mock_conn()
        conn.fetch = AsyncMock(return_value=[
            {"id": msg_id, "payload": payload_dict},
        ])
        conn.fetchrow = AsyncMock(return_value={"id": uuid4()})

        tx = AsyncMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=tx)

        pool = make_mock_pool(conn)
        worker = IdentifyWorker(db_pool=pool)
        result = await worker.run_batch()
        assert result == 1

    @pytest.mark.asyncio
    async def test_identify_payload_none(self):
        """Payload None — ca devrait pas planter, juste utiliser un dict vide."""
        msg_id = uuid4()

        conn = make_mock_conn()
        conn.fetch = AsyncMock(return_value=[
            {"id": msg_id, "payload": None},
        ])
        conn.fetchrow = AsyncMock(return_value=None)

        tx = AsyncMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=tx)

        pool = make_mock_pool(conn)
        worker = IdentifyWorker(db_pool=pool)
        result = await worker.run_batch()
        assert result == 1

    @pytest.mark.asyncio
    async def test_identify_multiple_messages(self):
        """Batch de 3 messages — tous doivent être traités."""
        messages = [
            {"id": uuid4(), "payload": {"data_category": "vector", "operation": "INSERT"}},
            {"id": uuid4(), "payload": {"data_category": "vector", "operation": "UPDATE"}},
            {"id": uuid4(), "payload": {"data_category": "raster", "operation": "DELETE"}},
        ]

        conn = make_mock_conn()
        conn.fetch = AsyncMock(return_value=messages)
        conn.fetchrow = AsyncMock(return_value={"id": uuid4()})

        tx = AsyncMock()
        tx.__aenter__ = AsyncMock(return_value=tx)
        tx.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=tx)

        pool = make_mock_pool(conn)
        worker = IdentifyWorker(db_pool=pool, batch_size=10)
        result = await worker.run_batch()
        assert result == 3

    def test_worker_type(self):
        assert IdentifyWorker.worker_type == WorkerType.IDENTIFY


# ===========================================================================
# DispatchWorker
# ===========================================================================


class TestDispatchWorker:
    """
    Beta : "Le DispatchWorker envoie des pg_notify pour les messages PROCESSED.
           J'ai testé : pg_notify désactivé, payload vide, erreur de notify..."
    """

    @pytest.mark.asyncio
    async def test_run_batch_no_pool_returns_zero(self):
        worker = DispatchWorker(db_pool=None)
        result = await worker.run_batch()
        assert result == 0

    @pytest.mark.asyncio
    async def test_run_batch_empty_queue(self):
        conn = make_mock_conn()
        conn.fetch = AsyncMock(return_value=[])
        pool = make_mock_pool(conn)
        worker = DispatchWorker(db_pool=pool)
        result = await worker.run_batch()
        assert result == 0

    @pytest.mark.asyncio
    async def test_dispatch_one_message(self):
        """Un message PROCESSED doit être dispatché et marqué COMPLETED."""
        msg_id = uuid4()
        payload = json.dumps({
            "dataset_id": str(uuid4()),
            "layer_id": str(uuid4()),
            "operation": "UPDATE",
        })

        conn = make_mock_conn()
        conn.fetch = AsyncMock(return_value=[
            {"id": msg_id, "payload": payload},
        ])
        pool = make_mock_pool(conn)
        worker = DispatchWorker(db_pool=pool)
        result = await worker.run_batch()

        assert result == 1
        # Au moins 3 execute : DISPATCHING, pg_notify, COMPLETED
        assert conn.execute.call_count >= 3

    @pytest.mark.asyncio
    async def test_dispatch_pg_notify_disabled(self):
        """pg_notify désactivé — le message passe quand même en COMPLETED."""
        msg_id = uuid4()
        payload = {"dataset_id": str(uuid4()), "operation": "INSERT"}

        conn = make_mock_conn()
        conn.fetch = AsyncMock(return_value=[
            {"id": msg_id, "payload": payload},
        ])
        pool = make_mock_pool(conn)
        worker = DispatchWorker(db_pool=pool, pg_notify_enabled=False)
        result = await worker.run_batch()

        assert result == 1
        # Pas de pg_notify => 2 execute seulement (DISPATCHING + COMPLETED)
        assert conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_dispatch_pg_notify_error_non_fatal(self):
        """
        Et si pg_notify plante ? Ca doit pas empêcher le message
        de passer en COMPLETED. C'est non-bloquant.
        """
        msg_id = uuid4()
        payload = json.dumps({"operation": "UPDATE"})

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Le 3e appel est le pg_notify — on le fait planter
            if call_count == 2 and "pg_notify" in str(args):
                raise ConnectionError("pg_notify failed!")
            return None

        conn = make_mock_conn()
        conn.fetch = AsyncMock(return_value=[
            {"id": msg_id, "payload": payload},
        ])
        conn.execute = AsyncMock(side_effect=execute_side_effect)
        pool = make_mock_pool(conn)
        worker = DispatchWorker(db_pool=pool)
        # Ca ne doit pas lever d'exception
        result = await worker.run_batch()
        assert result == 1

    @pytest.mark.asyncio
    async def test_dispatch_payload_dict_not_string(self):
        """Payload en dict au lieu de string JSON."""
        msg_id = uuid4()
        payload = {"dataset_id": str(uuid4()), "operation": "DELETE"}

        conn = make_mock_conn()
        conn.fetch = AsyncMock(return_value=[
            {"id": msg_id, "payload": payload},
        ])
        pool = make_mock_pool(conn)
        worker = DispatchWorker(db_pool=pool)
        result = await worker.run_batch()
        assert result == 1

    @pytest.mark.asyncio
    async def test_dispatch_payload_none(self):
        """Payload None — doit utiliser un dict vide, pas planter."""
        msg_id = uuid4()

        conn = make_mock_conn()
        conn.fetch = AsyncMock(return_value=[
            {"id": msg_id, "payload": None},
        ])
        pool = make_mock_pool(conn)
        worker = DispatchWorker(db_pool=pool)
        result = await worker.run_batch()
        assert result == 1

    @pytest.mark.asyncio
    async def test_dispatch_custom_channel(self):
        """Canal pg_notify personnalisé."""
        worker = DispatchWorker(
            db_pool=make_mock_pool(),
            pg_notify_channel="my_custom_channel",
        )
        assert worker.pg_notify_channel == "my_custom_channel"

    @pytest.mark.asyncio
    async def test_dispatch_multiple_messages(self):
        """Batch de 3 messages."""
        messages = [
            {"id": uuid4(), "payload": json.dumps({"operation": "INSERT"})},
            {"id": uuid4(), "payload": json.dumps({"operation": "UPDATE"})},
            {"id": uuid4(), "payload": json.dumps({"operation": "DELETE"})},
        ]
        conn = make_mock_conn()
        conn.fetch = AsyncMock(return_value=messages)
        pool = make_mock_pool(conn)
        worker = DispatchWorker(db_pool=pool, batch_size=10)
        result = await worker.run_batch()
        assert result == 3

    def test_worker_type(self):
        assert DispatchWorker.worker_type == WorkerType.DISPATCH

    def test_default_pg_channel(self):
        worker = DispatchWorker(db_pool=None)
        assert worker.pg_notify_channel == "gispulse_events"


# ===========================================================================
# PgNotifyChannel
# ===========================================================================


class TestPgNotifyChannel:
    """
    Beta : "Le PgNotifyChannel envoie des NOTIFY via asyncpg.
           J'ai mocké le pool et vérifié le payload JSON."
    """

    @pytest.mark.asyncio
    async def test_send_basic(self):
        conn = make_mock_conn()
        pool = make_mock_pool(conn)
        channel = PgNotifyChannel(db_pool=pool, channel_name="test_chan")
        await channel.send(operation="UPDATE", dataset_id="abc-123")

        conn.execute.assert_called_once()
        call_args = conn.execute.call_args.args
        assert call_args[0] == "SELECT pg_notify($1, $2)"
        assert call_args[1] == "test_chan"
        payload = json.loads(call_args[2])
        assert payload["operation"] == "UPDATE"
        assert payload["dataset_id"] == "abc-123"
        assert "timestamp" in payload

    @pytest.mark.asyncio
    async def test_send_without_optional_fields(self):
        """Envoyer sans dataset_id ni layer_id."""
        conn = make_mock_conn()
        pool = make_mock_pool(conn)
        channel = PgNotifyChannel(db_pool=pool)
        await channel.send(operation="PROCESS")

        payload = json.loads(conn.execute.call_args.args[2])
        assert "dataset_id" not in payload
        assert "layer_id" not in payload
        assert payload["operation"] == "PROCESS"

    @pytest.mark.asyncio
    async def test_send_with_extra(self):
        """Des données extra sont mergées dans le payload."""
        conn = make_mock_conn()
        pool = make_mock_pool(conn)
        channel = PgNotifyChannel(db_pool=pool)
        await channel.send(
            operation="INSERT",
            extra={"custom_key": "custom_value", "count": 42},
        )

        payload = json.loads(conn.execute.call_args.args[2])
        assert payload["custom_key"] == "custom_value"
        assert payload["count"] == 42

    @pytest.mark.asyncio
    async def test_send_with_all_fields(self):
        conn = make_mock_conn()
        pool = make_mock_pool(conn)
        channel = PgNotifyChannel(db_pool=pool, channel_name="spatial_events")
        await channel.send(
            operation="DELETE",
            dataset_id="ds-001",
            layer_id="ly-002",
            extra={"reason": "cleanup"},
        )

        payload = json.loads(conn.execute.call_args.args[2])
        assert payload["operation"] == "DELETE"
        assert payload["dataset_id"] == "ds-001"
        assert payload["layer_id"] == "ly-002"
        assert payload["reason"] == "cleanup"

    def test_default_channel_name(self):
        pool = make_mock_pool()
        channel = PgNotifyChannel(db_pool=pool)
        assert channel.channel == "gispulse_events"


# ===========================================================================
# PgNotifyListener
# ===========================================================================


class TestPgNotifyListener:
    """
    Beta : "Le Listener ouvre une connexion dédiée et écoute les NOTIFY.
           Que se passe-t-il si on stop() sans avoir start() ?
           Que se passe-t-il si remove_listener lève une erreur ?"
    """

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _HAS_ASYNCPG, reason="asyncpg not installed")
    async def test_start_connects_and_adds_listener(self):
        mock_conn = make_mock_conn()
        callback = MagicMock()

        with patch("gispulse.adapters.esb.pg_notify.asyncpg.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_conn
            listener = PgNotifyListener(dsn="postgresql://fake", channel_name="test")
            await listener.start(callback=callback)

            mock_connect.assert_awaited_once_with("postgresql://fake")
            mock_conn.add_listener.assert_awaited_once_with("test", callback)
            assert listener._conn is mock_conn
            assert listener._callback is callback

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _HAS_ASYNCPG, reason="asyncpg not installed")
    async def test_stop_removes_listener_and_closes(self):
        mock_conn = make_mock_conn()
        callback = MagicMock()

        with patch("gispulse.adapters.esb.pg_notify.asyncpg.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_conn
            listener = PgNotifyListener(dsn="postgresql://fake")
            await listener.start(callback=callback)
            await listener.stop()

            mock_conn.remove_listener.assert_awaited_once()
            mock_conn.close.assert_awaited_once()
            assert listener._conn is None

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        """Stop sans start — ca ne doit pas planter."""
        listener = PgNotifyListener(dsn="postgresql://fake")
        # _conn est None, ca doit être un no-op
        await listener.stop()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _HAS_ASYNCPG, reason="asyncpg not installed")
    async def test_stop_with_remove_listener_error(self):
        """
        Et si remove_listener lève une exception ?
        Le stop() doit quand même fermer la connexion.
        """
        mock_conn = make_mock_conn()
        mock_conn.remove_listener = AsyncMock(side_effect=RuntimeError("already removed"))
        callback = MagicMock()

        with patch("gispulse.adapters.esb.pg_notify.asyncpg.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_conn
            listener = PgNotifyListener(dsn="postgresql://fake")
            await listener.start(callback=callback)
            # Ne doit pas lever d'exception
            await listener.stop()
            # La connexion doit quand même être fermée
            mock_conn.close.assert_awaited_once()

    def test_default_channel(self):
        listener = PgNotifyListener(dsn="postgresql://fake")
        assert listener.channel == "gispulse_events"

    def test_custom_channel(self):
        listener = PgNotifyListener(dsn="postgresql://fake", channel_name="custom_ch")
        assert listener.channel == "custom_ch"


# ===========================================================================
# BaseWorker — quelques vérifications via les sous-classes concrètes
# ===========================================================================


class TestBaseWorkerViaSubclasses:
    """
    Beta : "Je ne teste pas BaseWorker directement (c'est abstrait),
           mais je vérifie que les sous-classes héritent bien du comportement."
    """

    def test_identify_worker_name_format(self):
        worker = IdentifyWorker(db_pool=None)
        assert worker.name.startswith("gispulse-identify-")

    def test_dispatch_worker_name_format(self):
        worker = DispatchWorker(db_pool=None)
        assert worker.name.startswith("gispulse-dispatch-")

    def test_get_stats_structure(self):
        worker = IdentifyWorker(db_pool=None)
        stats = worker.get_stats()
        assert "worker_id" in stats
        assert "worker_type" in stats
        assert stats["worker_type"] == "IDENTIFY"
        assert stats["is_running"] is False
        assert stats["messages_processed"] == 0

    def test_custom_worker_id(self):
        uid = uuid4()
        worker = IdentifyWorker(db_pool=None, worker_id=uid)
        assert worker.worker_id == uid

    def test_default_batch_size(self):
        worker = DispatchWorker(db_pool=None)
        assert worker.batch_size == 10
