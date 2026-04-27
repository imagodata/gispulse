"""Beta — Tests d'acceptation Lot 1 (tier gating local_triggers).

Ces tests couvrent les angles morts NON couverts par
``test_triggers_tier_gating.py`` (les 19 tests de Marco) :

  1. Conflit git non résolu dans triggers_router.py  -> BUG BLOQUANT
  2. Re-enable d'un trigger disabled qui ferait dépasser le cap
  3. Race condition sur le compteur "5 triggers actifs"
  4. Bypass via ``POST /triggers/{id}/operations`` (extra payload non gated)
  5. Bypass via ``POST /triggers/{id}/toggle`` qui ré-active sans vérif cap
  6. Tier-switch à chaud quand on a déjà N>5 triggers actifs en Pro
  7. Action types contournés (casse, type custom)
  8. ``conditions=None`` ou non-dict (skip silencieux du check)
  9. Cap=int / cascade comme str
 10. Demo mode bypass-mode interaction
 11. Format du message d'erreur 402 (parsable côté client)
 12. ``/triggers/{id}/evaluate`` autorisé en Community (pas de privilege escalation)

Lancer ::

    pytest tests/integration/http/test_triggers_tier_gating_edge_cases.py -v
"""

from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# Smoke import — détecte les conflits git / syntaxe avant tout
# ---------------------------------------------------------------------------


class TestModuleImportable:
    """BUG BLOCKER guard : le router doit être importable.

    Beta a constaté un conflit git non résolu dans triggers_router.py
    (lignes 42-46 : ``<<<<<<< Updated upstream`` / ``>>>>>>> Stashed changes``).
    Tant que ce test échoue, tous les autres tests d'intégration HTTP du
    suite explosent à la collection (SyntaxError).
    """

    def test_triggers_router_is_importable(self):
        # Si ce test échoue avec SyntaxError, le merge est cassé.
        import importlib

        mod = importlib.import_module(
            "gispulse.adapters.http.routers.triggers_router"
        )
        assert hasattr(mod, "router")
        assert hasattr(mod, "_enforce_community_trigger_caps")


# ---------------------------------------------------------------------------
# Fixtures (réutilisent le pattern de Marco)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _use_memory_storage(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    try:
        from gispulse.adapters.http.rate_limit import limiter

        limiter.reset()
    except Exception:
        pass
    yield


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from gispulse.adapters.http.app import create_app

    os.environ["GISPULSE_STORAGE"] = "memory"
    return TestClient(create_app())


def _community(monkeypatch) -> None:
    monkeypatch.setenv("GISPULSE_TIER", "community")
    monkeypatch.delenv("GISPULSE_LICENSE_KEY", raising=False)


def _trigger_payload(name: str = "t1", **overrides):
    payload = {
        "name": name,
        "description": "test",
        "event": "manual",
        "trigger_type": "api",
        "category": "data",
        "severity": "info",
        "conditions": {"table": "parcels"},
        "enabled": True,
        "auto_eval": False,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 1. Re-enable bypass : disabled trigger → toggle/PUT ré-active au-dessus du cap
# ---------------------------------------------------------------------------


class TestReEnableBypass:
    """BUG MAJEUR : un trigger désactivé peut être réactivé alors que
    5 autres sont déjà actifs, faisant grimper le compteur à 6 sans 402.

    Marco a documenté que les disabled ne comptent pas (c'est le cas pour
    la création), mais l'endpoint ``POST /triggers/{id}/toggle`` ne passe
    PAS par ``_enforce_community_trigger_caps`` du tout.
    """

    def test_toggle_can_re_enable_above_cap(self, client, monkeypatch):
        _community(monkeypatch)
        # Crée 1 trigger disabled (n'occupe pas de slot).
        disabled = client.post(
            "/triggers", json=_trigger_payload(name="dormant", enabled=False)
        )
        assert disabled.status_code == 201
        disabled_id = disabled.json()["id"]

        # Sature le cap : 5 actifs.
        for i in range(5):
            r = client.post(
                "/triggers", json=_trigger_payload(name=f"active_{i}")
            )
            assert r.status_code == 201, r.text

        # Tente de toggle le dormant → ferait 6 actifs.
        # ATTENDU : 402 (refus, on a déjà saturé).
        # OBSERVÉ probable : 200 (toggle ne vérifie rien) → BUG.
        r = client.post(f"/triggers/{disabled_id}/toggle")
        assert r.status_code == 402, (
            f"BUG: toggle a ré-activé un trigger au-dessus du cap "
            f"sans 402 (status={r.status_code}, body={r.text}). "
            f"Le 6e trigger actif est maintenant en place sans contrôle tier."
        )

    def test_put_can_re_enable_above_cap(self, client, monkeypatch):
        """Variante via PUT : update enabled False → True quand cap déjà plein.

        Le code de ``_enforce_community_trigger_caps`` exclut le trigger
        en cours d'update du compte, ce qui est OK pour un update qui
        garde l'état enabled. Mais si l'on PASSE de disabled à enabled
        et que les 5 autres sont déjà actifs, exclude_id écarte
        précisément le trigger qu'il faudrait ajouter au compte.
        Résultat : 5 actifs + 1 réactivé = 6, mais le check voit 5 et passe.
        """
        _community(monkeypatch)
        dormant = client.post(
            "/triggers", json=_trigger_payload(name="dormant", enabled=False)
        ).json()
        for i in range(5):
            r = client.post(
                "/triggers", json=_trigger_payload(name=f"active_{i}")
            )
            assert r.status_code == 201

        r = client.put(
            f"/triggers/{dormant['id']}",
            json=_trigger_payload(name="dormant", enabled=True),
        )
        assert r.status_code == 402, (
            f"BUG: PUT a ré-activé un trigger en dépassant le cap "
            f"(status={r.status_code}). exclude_id écarte précisément "
            f"le trigger qu'on ajoute en réalité."
        )


# ---------------------------------------------------------------------------
# 2. Action-type bypass via casse / variantes
# ---------------------------------------------------------------------------


class TestActionTypeCaseSensitivity:
    """Le check ``action_type in {webhook, http, outbound_webhook}`` est
    sensible aux clés. Beta vérifie que la normalisation lowercase couvre
    les variantes WEBHOOK / Http / outbound_action et que l'attaquant
    ne peut pas glisser un webhook via une clé non listée.
    """

    @pytest.mark.parametrize(
        "action_type",
        [
            "WEBHOOK",
            "Http",
            "OutBound_Webhook",
            "webhook ",  # trailing space
        ],
    )
    def test_uppercase_or_messy_action_type_still_blocked(
        self, client, monkeypatch, action_type
    ):
        _community(monkeypatch)
        conditions = {
            "table": "parcels",
            "actions": [{"action_type": action_type, "config": {"url": "x"}}],
        }
        r = client.post("/triggers", json=_trigger_payload(conditions=conditions))
        # Notice : "webhook " avec espace n'est PAS lower-stripé.
        # Beta s'attend à ce que ce soit bloqué (defense in depth).
        if action_type.strip().lower() in {"webhook", "http", "outbound_webhook"}:
            assert r.status_code == 402, (
                f"BUG: action_type={action_type!r} a contourné le check "
                f"(status={r.status_code}). Le check devrait .strip() les "
                f"types avant comparaison."
            )

    def test_nested_action_in_extra_field_bypasses_via_operations(
        self, client, monkeypatch
    ):
        """Bypass : créer un trigger Community légal, puis attacher un
        webhook via ``POST /triggers/{id}/operations`` avec ``extra``."""
        _community(monkeypatch)
        created = client.post(
            "/triggers", json=_trigger_payload(name="legal")
        ).json()

        # POST /triggers/{id}/operations n'est PAS gated par les caps.
        # extra={"webhook": "..."} se retrouve mergé dans op_dict puis
        # in fine dans trigger.conditions.operations[N], sans validation.
        r = client.post(
            f"/triggers/{created['id']}/operations",
            json={
                "phase": "after",
                "operation": "st_within",
                "field": "x",
                "distant_table": "y",
                "distant_field": "z",
                "extra": {"webhook": "https://attacker.example.com/leak"},
            },
        )
        # Beta veut un 402 ou au moins un 422.
        assert r.status_code in (402, 422), (
            f"BUG: operations endpoint accepte un webhook URL via extra "
            f"sans gate tier (status={r.status_code}). "
            f"Vector de privilege escalation Community → ESB-like."
        )

    @pytest.mark.parametrize(
        "forbidden_extra",
        [
            {"webhook": "https://attacker.example.com/leak"},
            {"webhook_url": "https://attacker.example.com"},
            {"outbound_action": {"url": "x"}},
            {"outbound_url": "https://x"},
            {"cron": "*/5 * * * *"},
            {"cron_schedule": "* * * * *"},
            {"dlq": True},
            {"dlq_enabled": True},
            {"cascade": 5},
            {"cascade_depth": 3},
            {"http": {"url": "https://x"}},
            {"http_url": "https://x"},
            # Casing / whitespace variants — must be normalised.
            {"WebHook": "https://x"},
            {" webhook ": "https://x"},
            # action_type-as-value vector inside extra.
            {"action_type": "webhook"},
            {"action_type": " WEBHOOK "},
        ],
    )
    def test_add_operation_blocks_forbidden_extra_keys(
        self, client, monkeypatch, forbidden_extra
    ):
        """Bug #2 — exhaustive coverage of forbidden keys + casing variants."""
        _community(monkeypatch)
        created = client.post(
            "/triggers", json=_trigger_payload(name="legal2")
        ).json()
        r = client.post(
            f"/triggers/{created['id']}/operations",
            json={
                "phase": "after",
                "operation": "st_within",
                "field": "x",
                "distant_table": "y",
                "distant_field": "z",
                "extra": forbidden_extra,
            },
        )
        assert r.status_code == 402, (
            f"BUG: extra={forbidden_extra!r} a contourné le gate "
            f"(status={r.status_code}). Vector ESB-escalation."
        )

    def test_update_operation_blocks_forbidden_extra_keys(
        self, client, monkeypatch
    ):
        """Bug #2 — vector identique côté PUT operations.

        Workflow : créer le trigger + une op légale en Pro (default tier),
        switch Community, puis PUT l'op avec un extra interdit → 402.
        """
        # 1. Setup en Pro pour bypasser le gate à l'add.
        created = client.post(
            "/triggers", json=_trigger_payload(name="legal_put")
        ).json()
        add = client.post(
            f"/triggers/{created['id']}/operations",
            json={
                "phase": "after",
                "operation": "st_within",
                "field": "x",
                "distant_table": "y",
                "distant_field": "z",
            },
        )
        assert add.status_code == 201, add.text
        op_id = add.json()["op_id"]

        # 2. Switch Community puis tente l'update avec extra interdit.
        _community(monkeypatch)
        r = client.put(
            f"/triggers/{created['id']}/operations/{op_id}",
            json={
                "phase": "after",
                "operation": "st_within",
                "field": "x",
                "distant_table": "y",
                "distant_field": "z",
                "extra": {"webhook": "https://attacker.example.com"},
            },
        )
        assert r.status_code == 402, (
            f"BUG: update_operation accepte un webhook URL via extra "
            f"(status={r.status_code}). Mêmes contraintes que add_operation."
        )

    def test_pro_tier_can_use_webhook_in_operation_extra(self, client):
        """Pro+ : pas de cap, les keys interdites en Community passent."""
        # Default test fixture pin tier=pro.
        created = client.post(
            "/triggers", json=_trigger_payload(name="pro_op")
        ).json()
        r = client.post(
            f"/triggers/{created['id']}/operations",
            json={
                "phase": "after",
                "operation": "st_within",
                "field": "x",
                "distant_table": "y",
                "distant_field": "z",
                "extra": {"webhook": "https://hook.gispulse.io/notify"},
            },
        )
        assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# 3. Conditions invalides / non-dict
# ---------------------------------------------------------------------------


class TestConditionsTypeRobustness:
    """Le check ``isinstance(conditions, dict)`` exit silencieusement
    sur une liste, un str, ou None. Vérifie que ce n'est pas un vecteur."""

    @pytest.mark.parametrize(
        "conditions",
        [
            None,
            [],
            "raw_string",
            42,
        ],
    )
    def test_non_dict_conditions_do_not_break_caps(
        self, client, monkeypatch, conditions
    ):
        _community(monkeypatch)
        # Pas un bypass évident, mais Beta veut s'assurer qu'au pire
        # c'est un 422 (validation) plutôt qu'un 500.
        r = client.post(
            "/triggers", json=_trigger_payload(conditions=conditions)
        )
        assert r.status_code in (201, 422), (
            f"non-dict conditions={conditions!r} → status={r.status_code}, "
            f"body={r.text[:200]} (attendu 201 ou 422, pas 5xx)."
        )


# ---------------------------------------------------------------------------
# 4. Race condition sur le cap — concurrence
# ---------------------------------------------------------------------------


class TestConcurrentCreateRace:
    """Beta n'aime pas les checks séquentiels sur un compteur partagé.

    Si 6 clients POST /triggers en parallèle alors que 0 trigger existe,
    le check ``active >= 5`` peut voir ``active=4`` pour les 6 → 6 créés.

    Note : avec TestClient (sync, in-process, mémoire), la fenêtre est
    minuscule mais existe (GIL switching). En vrai sidecar avec
    plusieurs workers gunicorn, le bug est garanti.
    """

    def test_six_parallel_creates_should_cap_at_five(self, client, monkeypatch):
        _community(monkeypatch)
        import concurrent.futures

        def _create(i):
            return client.post(
                "/triggers", json=_trigger_payload(name=f"race_{i}")
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(_create, range(8)))

        statuses = [r.status_code for r in results]
        created = sum(1 for s in statuses if s == 201)
        assert created <= 5, (
            f"BUG (race condition): {created} triggers créés en concurrent "
            f"(cap=5). Statuses={statuses}. Le check ``active >= 5`` "
            f"sur ``repo.list_all()`` n'est pas atomique. Reco : verrou "
            f"applicatif autour du save() ou compteur Redis INCR."
        )


# ---------------------------------------------------------------------------
# 5. Format du message 402 — parsable côté client ?
# ---------------------------------------------------------------------------


class TestErrorMessageContract:
    """Le client (UI portal, SDK) doit pouvoir afficher un upgrade CTA.

    Beta vérifie que le 402 retourne :
      * Un detail string non vide
      * Une URL d'upgrade détectable (https://gispulse.com/pricing OU mention "Pro")
    Idéalement un JSON structuré avec ``feature`` et ``upgrade_url``,
    mais à défaut, au moins un substring exploitable.
    """

    def test_402_contains_pro_upgrade_hint(self, client, monkeypatch):
        _community(monkeypatch)
        for i in range(5):
            client.post("/triggers", json=_trigger_payload(name=f"a_{i}"))

        r = client.post("/triggers", json=_trigger_payload(name="six"))
        assert r.status_code == 402

        body = r.json()
        # GISPulse standard envelope: {"error": {"code", "message", "detail"}}
        err = body.get("error", body) if isinstance(body, dict) else {}
        message = err.get("message", "") if isinstance(err, dict) else ""
        code = err.get("code", "") if isinstance(err, dict) else ""

        assert "Pro" in message, (
            f"402 message ne contient pas 'Pro' : {message!r}. "
            f"Le client UI ne peut pas afficher de CTA générique."
        )
        # Beta note (à clarifier avec Jordan PO) : pas de champ structuré
        # ``upgrade_url`` ni ``feature`` machine-readable. Tout en texte
        # libre. Le SDK doit parser le message pour récupérer l'URL.
        # Pas un bug bloquant, mais à durcir.
        assert code == "HTTP_402", (
            f"Code d'erreur non standardisé : {code!r}. "
            f"402 n'est pas dans status_to_code → code générique HTTP_402."
        )


# ---------------------------------------------------------------------------
# 6. Tier switch à chaud (Pro → Community avec >5 triggers actifs)
# ---------------------------------------------------------------------------


class TestTierDowngradeBehaviour:
    """Edge case business : un compte Pro qui a 12 triggers + cron + webhook
    redescend en Community (fin d'abonnement). Que se passe-t-il ?

    Comportement actuel (à documenter, pas un bug a priori) :
      * Les triggers existants restent en BDD (read OK)
      * GET /triggers liste les 12 (cap n'est PAS rétro-actif)
      * POST /triggers est bloqué (cap atteint)
      * PUT /triggers/{id} sur un existant en Pro est bloqué si
        webhook/cron présent dans conditions
    Beta veut juste que ça soit stable et documenté.
    """

    def test_pro_creates_12_then_community_lists_all_12(
        self, client, monkeypatch
    ):
        # Phase Pro : crée 12 triggers + un avec webhook.
        for i in range(12):
            r = client.post(
                "/triggers", json=_trigger_payload(name=f"pro_{i}")
            )
            assert r.status_code == 201, r.text

        # Switch à community.
        _community(monkeypatch)

        # Lecture toujours OK.
        r = client.get("/triggers")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 12

        # Création nouvelle bloquée.
        r = client.post("/triggers", json=_trigger_payload(name="thirteenth"))
        assert r.status_code == 402

    def test_pro_creates_webhook_then_community_update_blocks_keep_webhook(
        self, client, monkeypatch
    ):
        """Edge case nasty : un trigger avec webhook créé en Pro,
        puis le user passe en Community et tente un PUT pour juste
        renommer le trigger en gardant le webhook → ATTENDU 402."""
        created = client.post(
            "/triggers",
            json=_trigger_payload(
                name="pro_hook",
                conditions={"table": "p", "webhook": "https://hook"},
            ),
        )
        assert created.status_code == 201
        tid = created.json()["id"]

        _community(monkeypatch)
        # PUT avec le même webhook (juste rename) → DOIT être 402,
        # même si le trigger existait avant le switch.
        r = client.put(
            f"/triggers/{tid}",
            json=_trigger_payload(
                name="renamed",
                conditions={"table": "p", "webhook": "https://hook"},
            ),
        )
        assert r.status_code == 402, (
            f"Edge case : PUT en Community sur trigger Pro avec webhook "
            f"a status={r.status_code} (attendu 402)."
        )


# ---------------------------------------------------------------------------
# 7. Demo mode interaction
# ---------------------------------------------------------------------------


class TestDemoModeInteraction:
    """``GISPULSE_DEMO_MODE=true`` + token valide → check tier bypassé.

    Sans token valide (cas par défaut) : pas de bypass, on garde Community.
    """

    def test_demo_mode_without_token_does_not_bypass_caps(
        self, client, monkeypatch
    ):
        _community(monkeypatch)
        monkeypatch.setenv("GISPULSE_DEMO_MODE", "true")
        # Pas de GISPULSE_DEMO_TOKEN ni de DIGEST → _demo_token_valid()=False.
        for i in range(5):
            assert (
                client.post(
                    "/triggers", json=_trigger_payload(name=f"d_{i}")
                ).status_code
                == 201
            )
        r = client.post("/triggers", json=_trigger_payload(name="six"))
        assert r.status_code == 402, (
            f"BUG SÉCURITÉ : demo_mode=true sans token a bypassé le cap "
            f"Community (status={r.status_code}). Tout user qui set "
            f"GISPULSE_DEMO_MODE=true sur sa copie OSS pourrait "
            f"contourner les caps."
        )


# ---------------------------------------------------------------------------
# 8. /triggers/{id}/evaluate en Community — pas de privilege escalation
# ---------------------------------------------------------------------------


class TestEvaluateInCommunity:
    """Le endpoint /evaluate est Community-OK, mais Beta vérifie qu'il
    ne peut pas être détourné pour exécuter une action webhook
    configurée dans un trigger qui aurait échappé au cap (defense in depth)."""

    def test_evaluate_does_not_dispatch_webhook_action(self, client, monkeypatch):
        # On force-grade en Pro pour CRÉER un trigger avec webhook.
        # (Marco's tests fixture default tier=pro.)
        created = client.post(
            "/triggers",
            json=_trigger_payload(
                name="hook",
                conditions={
                    "table": "p",
                    "actions": [
                        {
                            "action_type": "webhook",
                            "config": {"url": "https://attacker.example.com"},
                        }
                    ],
                },
            ),
        )
        assert created.status_code == 201
        tid = created.json()["id"]

        # Switch à community puis evaluate.
        _community(monkeypatch)
        r = client.post(
            f"/triggers/{tid}/evaluate",
            json={"records": []},
        )
        # Beta : avec records=[], pas de fired. Vérifie au moins que le
        # endpoint n'a pas tenté d'appeler le webhook (status 200, liste vide).
        assert r.status_code == 200, r.text
        assert r.json() == [], (
            "evaluate avec records=[] devrait retourner [] sans dispatcher."
        )
