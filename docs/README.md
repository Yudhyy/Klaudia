# Klaudia documentation

Klaudia's public documentation uses Mintlify. The English site lives in `en/`,
with its navigation and settings in `en/docs.json`.

## Preview locally

Install the [Mintlify CLI](https://www.mintlify.com/docs/cli/install) with a
supported Node.js version:

```bash
npm install -g mint
```

From the repository root, start the preview:

```bash
cd docs/en
mint dev
```

Open the local URL printed by the CLI. Run Mintlify from `docs/en`, where
`docs.json` lives.

## Edit and check pages

Pages use Markdown or MDX. Read the
[documentation contribution guide](en/05-development/contributing/documentation.mdx)
for page structure, navigation, images, and writing guidance.

Before submitting a change, run these commands from `docs/en`:

```bash
mint validate
mint broken-links --check-anchors
```

## Directory layout

| Path | Content |
| --- | --- |
| `en/docs.json` | English site settings and navigation |
| `en/05-development/architecture/` | Backend and database overviews |
| `en/05-development/contributing/` | Contributor guides |
| `en/05-development/research/` | Research reports and their images |
| `en/05-development/img/` | Shared development diagrams |
| `IRONCALC_CAPABILITIES.md` and `TYPED_FORMULAS.md` | Standalone spreadsheet references outside the published site |

## Languages

The documentation is currently in English. Support for more languages is planned.
For now, add and update pages in `en/`.
