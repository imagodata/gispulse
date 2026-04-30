---
title: Lancer le portail localement
description: Guide pas-à-pas pour faire tourner le workbench GISPulse Portal sur votre machine via `gispulse portal`, sans HTTPS, sans CORS, sans plugin GIS.
---

# Lancer le portail localement

Le **GISPulse Portal** est un éditeur visuel (canvas de noeuds, registre de capabilities, gestionnaire de datasets) servi par le moteur GISPulse. Cette page documente la commande `gispulse portal`, qui mounte le SPA sur votre moteur local et ouvre le navigateur.

> **Axiome produit.** CLI et portail sont deux UIs équivalentes sur la même source de vérité (`triggers.yaml` + change-log). Tout ce que vous configurez via le portail est éditable via le CLI, et inversement.

## Quick start (30 secondes)

```bash
# 1. Installer le CLI + le bundle SPA
pipx install gispulse-portal

# 2. Lancer le moteur + ouvrir le navigateur sur localhost:8001/portal
gispulse portal
```

C'est tout. Le navigateur s'ouvre sur `http://127.0.0.1:8001/portal/`, vous éditez vos triggers, et `Ctrl+C` arrête le serveur.

::: tip Pourquoi deux packages ?
`gispulse-portal` est un wheel PyPI distinct qui ship le SPA build (`dist/`). Il dépend de `gispulse`, donc `pipx install gispulse-portal` installe les deux d'un coup. Ce découpage évite de gonfler `gispulse` (resté lean ~3 MB) pour les utilisateurs CLI-only / CI-only. Voir le [billet d'architecture](/guide/architecture) pour le détail.
:::

## La commande `gispulse portal`

```bash
gispulse portal [OPTIONS]
```

| Option | Défaut | Description |
|--------|--------|-------------|
| `--port`, `-p` | `8001` | Port d'écoute du moteur local. |
| `--host` | `127.0.0.1` | Hôte d'écoute. Garder `127.0.0.1` sauf besoin LAN. |
| `--data-dir`, `-d` | `~/.gispulse/data` | Répertoire pour les datasets uploadés via le portail. |
| `--no-browser` | `false` | Ne pas ouvrir le navigateur (utile pour SSH / headless). |
| `--backend URL` | — | Mode "remote" : ouvre le portail GH Pages pointé sur un moteur distant, **n'instancie pas** de moteur local. |
| `--dev` | `false` | Autorise le fallback sur `portal/dist/` du checkout local (workflow contributeur). |

### Comportement

1. **Resolve du SPA bundlé.** La commande cherche `gispulse_portal.PORTAL_DIST_PATH`. Si introuvable et `--dev` actif, fallback sur `<repo>/portal/dist/`.
2. **Mount same-origin.** Le SPA est servi par FastAPI sous `/portal` via `StaticFiles`. Le moteur (API REST + WebSocket) est servi sur la racine. Pas de mixed-content, pas de CORS — tout passe par `localhost:8001`.
3. **Healthcheck + ouverture du navigateur.** Un thread daemon poll `GET /health` toutes les 100 ms pendant 3 s, puis appelle `webbrowser.open()` sur l'URL du portail. Si le healthcheck timeout, on ouvre quand même — au pire vous voyez le loader.
4. **Uvicorn run.** Le moteur tourne en foreground. `Ctrl+C` arrête proprement.

### Exemples

```bash
# Port custom (conflit sur 8001)
gispulse portal --port 9000

# Pas de navigateur (déploiement SSH, container, CI)
gispulse portal --no-browser

# Datasets dans un répertoire projet
gispulse portal --data-dir ./my-project/data

# Workflow contributeur (depuis un checkout du repo)
gispulse portal --dev
```

## Sans le package `gispulse-portal`

Si vous avez installé uniquement `gispulse` (sans le bundle SPA), la commande échoue proprement avec un message d'install :

```
$ gispulse portal
Error: gispulse-portal package is not installed.
Install it with:
  pip install gispulse-portal
Or, for a remote workbench without a local install:
  gispulse portal --backend=https://your-engine.example.com
```

C'est intentionnel : les utilisateurs CLI-only (CI/CD, serveurs headless, power users terminal) ne payent pas le coût du SPA. Si vous voulez juste l'API REST + WebSocket sans UI, utilisez plutôt [`gispulse engine`](/guide/engine).

## Mode remote : `--backend URL`

Plutôt qu'un moteur local, vous pouvez pointer le portail GH Pages (servi en HTTPS) sur **un moteur déjà déployé** quelque part :

```bash
gispulse portal --backend=https://your.engine.example.com
```

La commande encode le paramètre dans la querystring et ouvre :

```
https://gispulse.dev/?backend=https%3A%2F%2Fyour.engine.example.com
```

**Cas d'usage :**

- Vous avez un moteur sur un VPS (cf. [Déploiement](/guide/deployment)) et voulez juste le piloter depuis votre poste.
- Vous évaluez la version SaaS hébergée (v1.6+, `pro.gispulse.dev`).
- Vous démontrez le portail à un client sans installer Python sur leur poste.

::: warning Mixed-content
Le portail GH Pages est servi en HTTPS. Le `backend` doit lui aussi être HTTPS — un `http://localhost:8001` ne fonctionnera pas (le navigateur bloque). Pour un moteur local, utilisez le mode par défaut (sans `--backend`), qui sert le SPA same-origin et règle le problème.
:::

## Troubleshooting

### Conflit de port (`EADDRINUSE`)

Le port `8001` est déjà pris (par exemple un moteur précédent qui n'a pas été tué). Diagnostique :

```bash
# Linux / macOS
lsof -i :8001

# Windows
netstat -ano | findstr :8001
```

Solution : `gispulse portal --port 9000`.

### Pare-feu (Windows / Corporate)

Au premier lancement, Windows Defender peut demander l'autorisation pour `python.exe` ou `uvicorn`. Acceptez sur **Réseaux privés** uniquement — pas besoin d'exposer le port en public.

### Healthcheck timeout (~3 s)

Si le moteur met plus de 3 s à démarrer (datasets volumineux, GDAL lent, container froid), le navigateur s'ouvre quand même mais sur un endpoint pas encore prêt. Rafraîchir une fois suffit. Pour des démarrages systématiquement lents, ouvrir manuellement après quelques secondes :

```bash
gispulse portal --no-browser
# attendre que le moteur log "Application startup complete"
# puis ouvrir http://127.0.0.1:8001/portal/ à la main
```

### Le navigateur ne s'ouvre pas du tout

`webbrowser.open()` échoue silencieusement sur certains environnements (WSL sans `wslview`, conteneur Docker, SSH X-forward cassé). L'URL est imprimée sur stdout :

```
GISPulse Portal at http://127.0.0.1:8001/portal/
```

Copier-coller l'URL dans votre navigateur.

### Gérer le processus moteur

`gispulse portal` reste en foreground. Pour le faire tourner en arrière-plan :

```bash
# Linux / macOS
gispulse portal --no-browser &
echo $!  # PID

# tmux / screen
tmux new -d -s gispulse 'gispulse portal --no-browser'

# systemd user unit (production locale persistante)
# voir /guide/deployment
```

## Voir aussi

- [`gispulse engine`](/guide/engine) — moteur sans SPA (API REST + WebSocket only).
- [CLI Référence](/guide/cli) — toutes les commandes.
- [Architecture](/guide/architecture) — pourquoi deux packages PyPI.
- [Déploiement](/guide/deployment) — production multi-user (PostGIS, Caddy, Docker).
