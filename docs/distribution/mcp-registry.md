# Publier le serveur MCP GISPulse dans les registres

Ce document décrit comment référencer le serveur MCP de GISPulse (`gispulse mcp`)
dans le registre MCP officiel et dans les annuaires tiers (Smithery, mcp.so,
Glama). Il a été rédigé le 2026-11-01 à partir de la documentation en ligne
alors en vigueur ; les sources consultées sont listées en fin de document.

> Les étapes marquées **[COMPTE PROPRIÉTAIRE]** exigent un compte que seul le
> propriétaire du dépôt peut utiliser (GitHub `imagodata`, compte Smithery, etc.).
> Aucune ne peut être faite à sa place.

## 1. Ce qui est publié

| Élément | Valeur |
|---|---|
| Commande | `gispulse mcp` (transport **stdio** uniquement, voir `src/gispulse/cli_mcp.py`) |
| Extra PyPI requis | `gispulse[mcp]` (`fastmcp>=2.14.2,<4.0`, `fiona`) |
| Paquet PyPI | `gispulse` 2.6.0 |
| Nom dans le registre | `io.github.imagodata/gispulse` |
| Manifeste | `server.json` à la racine du dépôt |
| Variable d'environnement lue | `GISPULSE_MCP_WORKDIR` (répertoire auquel tous les chemins d'outils sont confinés ; défaut : répertoire courant du processus) |

Surface exposée (17 outils + ressources `gispulse://...`) : catalogue de
capabilities, templates, catalogue de sources, inspection de jeux de données,
validation de pipelines, chargement/validation/dry-run de `triggers.yaml`.
Détails : [`docs-site/guide/mcp.md`](../../docs-site/guide/mcp.md).

Le client doit finir par exécuter l'équivalent de :

```bash
uvx --from "gispulse[mcp]" gispulse mcp
```

Le `server.json` exprime cela avec `runtimeHint: uvx`, un `runtimeArguments`
`--with gispulse[mcp]==2.6.0` (extra installé dans le même environnement que le
paquet), puis `packageArguments: [mcp]`. C'est le même schéma que d'autres
serveurs PyPI déjà présents dans le registre (par exemple `codenib`, qui utilise
`--with codenib[mcp]==x.y.z` suivi de l'argument positionnel `mcp`). La
commande résultante est `uvx --with "gispulse[mcp]==2.6.0" gispulse==2.6.0 mcp` ;
la syntaxe exacte de la ligne de commande finale dépend du client MCP, qui
assemble `runtimeHint` + `runtimeArguments` + identifiant + `packageArguments`.
Valider le comportement dans au moins un client (Claude Desktop) après publication.

Validation du manifeste : `server.json` a été validé contre le schéma
`https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`
(description de 94 caractères, limite 100). L'index
`https://static.modelcontextprotocol.io/schemas/` renvoie 404 (pas de listing) ;
la version `2025-12-11` est celle citée par le guide officiel de publication au
moment de la rédaction. Avant publication, relancer `mcp-publisher validate`.

## 2. Prérequis

1. **[COMPTE PROPRIÉTAIRE]** Accès GitHub au compte `imagodata`. Ce compte est de
   type *User* (pas une organisation) d'après l'API GitHub, donc le nom
   `io.github.imagodata/gispulse` est dans l'espace de noms personnel de ce
   compte : `mcp-publisher login github` suffit, à condition de s'authentifier
   **avec le compte `imagodata`** (et non `sducournau`).
   - Si `imagodata` devenait une organisation : le flux interactif ne donne pas
     l'espace de noms d'organisation (limitation documentée, issue
     modelcontextprotocol/registry#1468) ; il faudrait un PAT avec `read:org`
     (`mcp-publisher login github --token <PAT>`) ou GitHub Actions (OIDC).
2. **Une version de `gispulse` sur PyPI dont le README contient la ligne de
   vérification de propriété** :

   ```markdown
   <!-- mcp-name: io.github.imagodata/gispulse -->
   ```

   Le registre vérifie que la chaîne `mcp-name: <nom du serveur>` figure dans la
   description PyPI du paquet (le README). La chaîne peut être dans un commentaire
   HTML mais doit être suivie d'une frontière (retour ligne, espace, `-->`), sans
   caractère collé (pas de point final). État au 2026-11-01 : la version PyPI
   `2.5.1` **ne contient pas** cette ligne (vérifié via
   `https://pypi.org/pypi/gispulse/json`). La ligne est en cours d'ajout au README
   sur une autre branche ; elle ne sera sur PyPI qu'**à partir de la prochaine
   release**. Conséquence : tant que cette release n'est pas publiée, la
   publication échoue avec « Registry validation failed for package ». Il faudra
   alors aligner `version` et `packages[].version` de `server.json` sur la
   release qui contient la ligne (2.5.2 ou 2.6.0), puis publier.
3. `mcp-publisher` installé (voir §3.1).

## 3. Registre officiel (registry.modelcontextprotocol.io)

Le registre est en préversion (« preview ») : des changements ou remises à zéro
sont possibles. Il n'héberge que des métadonnées, pas les artefacts.

### 3.1 Installer `mcp-publisher`

```bash
# macOS / Linux
curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" | tar xz mcp-publisher && sudo mv mcp-publisher /usr/local/bin/
# ou : brew install mcp-publisher
mcp-publisher --help
```

### 3.2 Valider, s'authentifier, publier

Depuis la racine du dépôt (là où se trouve `server.json`) :

```bash
mcp-publisher validate                 # valide server.json sans publier
mcp-publisher login github             # [COMPTE PROPRIÉTAIRE] flux « device » : ouvrir
                                       # https://github.com/login/device, saisir le code,
                                       # autoriser avec le compte imagodata
mcp-publisher publish                  # envoie server.json au registre
```

### 3.3 Vérifier après publication

```bash
curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.imagodata/gispulse"
curl "https://registry.modelcontextprotocol.io/v0/servers?search=gispulse"
```

Les deux endpoints répondaient le 2026-11-01 (`{"servers":[],"metadata":{"count":0}}`
avant toute publication). Le guide officiel actuel utilise `/v0.1/` ; `/v0/`
fonctionnait encore. Contrôler que `name`, `version` et `packages[0].identifier`
correspondent, puis tester l'installation dans un client (voir §6).

### 3.4 Publications suivantes

À chaque release de `gispulse` : mettre à jour `version` et `packages[0].version`
dans `server.json` **et** `runtimeArguments[0].value` (`gispulse[mcp]==X.Y.Z`),
puis `mcp-publisher publish`. Automatisation possible dans un workflow GitHub
Actions avec `mcp-publisher login github-oidc` (permission `id-token: write`) ;
voir le guide `github-actions` du registre.

## 4. Smithery

Constat au 2026-11-01 (documentation `https://smithery.ai/docs/build/publish.md`) :
**`smithery.yaml` n'est plus le mécanisme de publication.** Aucune mention de
`smithery.yaml` dans la documentation, et les anciennes pages de configuration
Python/stdio renvoient 404. Smithery propose désormais deux voies :

- **URL** : un serveur déjà hébergé, exposé en HTTP *streamable* (avec OAuth si
  nécessaire), publié via `https://smithery.ai/new` (redirige vers
  `https://smithery.ai/servers/new` ; connexion requise). Ne convient pas : GISPulse
  est stdio uniquement.
- **Local (bundle MCPB)** : un serveur stdio distribué comme bundle `.mcpb`
  (`smithery mcp publish ./server.mcpb -n <namespace>/<serveur>`). La spécification
  MCPB prévoit les types `python` et `uv` (dépendances déclarées dans
  `pyproject.toml` et installées par l'hôte avec UV), mais recommande Node.js pour
  limiter la friction d'installation.

Décision : **aucun `smithery.yaml` n'est créé** (il ne serait pas lu). Un listing
Smithery demande de produire un bundle MCPB (`manifest.json` de type `uv` ou
`python`) et de le publier avec un compte Smithery. À traiter seulement si la
demande utilisateur le justifie : c'est du travail de packaging distinct, et le
chemin le plus court reste le registre officiel + `uvx`.

Étapes si on décide de le faire **[COMPTE PROPRIÉTAIRE : Smithery]** :
1. Créer un compte sur `https://smithery.ai` et un namespace.
2. Construire le bundle (spécification : `https://github.com/modelcontextprotocol/mcpb`).
3. `smithery mcp publish ./server.mcpb -n <namespace>/gispulse`, puis suivre le
   parcours de publication.

Alternative non publique : « Uplink » (`smithery mcp add --id gispulse -- uvx --from "gispulse[mcp]" gispulse mcp`)
expose un serveur local comme connexion Smithery, sans le lister publiquement.

## 5. mcp.so et Glama

- **mcp.so** : formulaire `https://mcp.so/submit?type=server` (type *MCP Server*,
  nom, URL du dépôt `https://github.com/imagodata/gispulse`). Connexion requise
  (« Sign In » dans l'en-tête). La page propose en plus une soumission payante
  (39 USD, publication immédiate, badge) ; le parcours gratuit passe par une
  revue. Le détail exact du parcours gratuit n'a pas pu être vérifié (page rendue
  côté client). **[COMPTE PROPRIÉTAIRE]**
- **Glama** : bouton « Add Server » sur `https://glama.ai/mcp/servers`. Le
  formulaire demande un nom (≤ 100 caractères), une description (≤ 400) et l'URL
  du dépôt GitHub ; il est destiné aux serveurs dont le code est sur GitHub et
  que l'utilisateur exécute lui-même (c'est le cas). Un compte Glama est
  probablement nécessaire (non vérifié). **[COMPTE PROPRIÉTAIRE]**

Texte réutilisable (≤ 400 caractères) :

> GISPulse est un moteur de règles SIG open source (AGPL) : 144 capabilities
> géospatiales, templates métier (PLU, PPRI, DVF, ZAN, FTTH), catalogue de sources
> open data FR/BE et dry-run de triggers. Le serveur MCP (stdio) permet à un agent
> de parcourir les capabilities, inspecter un GeoPackage et valider des règles sans
> effet de bord. `uvx --from "gispulse[mcp]" gispulse mcp`

## 6. Configuration client prête à coller

Prérequis côté utilisateur : [`uv`](https://docs.astral.sh/uv/) installé.

### Claude Code

```bash
claude mcp add gispulse -- uvx --from "gispulse[mcp]" gispulse mcp
```

Avec confinement des chemins à un dossier de travail :

```bash
claude mcp add gispulse -e GISPULSE_MCP_WORKDIR=/chemin/vers/projet -- uvx --from "gispulse[mcp]" gispulse mcp
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "gispulse": {
      "command": "uvx",
      "args": ["--from", "gispulse[mcp]", "gispulse", "mcp"],
      "env": {
        "GISPULSE_MCP_WORKDIR": "/chemin/vers/projet"
      }
    }
  }
}
```

### Déjà installé via pipx

```bash
pipx install "gispulse[mcp]"
claude mcp add gispulse -- gispulse mcp
```

## 7. Sources consultées (2026-11-01)

| Source | Statut |
|---|---|
| `https://raw.githubusercontent.com/modelcontextprotocol/registry/main/docs/modelcontextprotocol-io/quickstart.mdx` | lu |
| `.../package-types.mdx`, `.../authentication.mdx`, `.../registry-aggregators.mdx`, `.../github-actions.mdx` | lus |
| `.../docs/reference/server-json/generic-server-json.md`, `official-registry-requirements.md`, `.../cli/commands.md` | lus |
| `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json` | lu, utilisé pour valider |
| `https://static.modelcontextprotocol.io/schemas/` | 404 (pas d'index) |
| `https://raw.githubusercontent.com/modelcontextprotocol/registry/main/docs/guides/publishing/publish-server.md` | n'existe plus ; remplacé par `modelcontextprotocol-io/quickstart.mdx` |
| `https://smithery.ai/docs/llms.txt`, `/docs/build/publish.md`, `/docs/build/index.md`, `/docs/use/uplink.md` | lus |
| `https://mcp.so/submit?type=server`, `https://glama.ai/mcp/servers` (+ script `AddMcpServerModal`) | lus (pages JS, partiellement) |
| `https://registry.modelcontextprotocol.io/v0/servers?search=gispulse` | 200, vide |
