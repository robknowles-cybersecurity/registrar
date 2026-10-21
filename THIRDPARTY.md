# Third-party code and assets

**None.** This repository contains no third-party code, libraries, fonts,
images, icons or stylesheets. A search of the tree on 2026-10-20 for
vendored packages, licence or copyright headers, and font, image and
stylesheet files from elsewhere found none.

Notes, so the claim can be checked:

- **Runtime:** the Python 3 standard library only. Nothing is vendored.
- **PyYAML** is used *if it is already installed* and is not bundled or
  required; without it Registrar uses its own small YAML reader
  (`cpebot/_minyaml.py`, written for this project).
- **Fonts:** the web app and the ceu.tools page use the system font stack;
  no font files are shipped or fetched.
- **Icons:** `site/favicon.svg` was drawn for this project.
- **External services:** the optional Telegram capture bot talks to the
  Telegram Bot API over HTTPS. That is a network service the operator
  chooses to use, not code included here.
- **Rules data:** `bodies.yaml` records facts about certifying bodies'
  published rules (caps, minimums, cycle lengths, fees) with links to the
  source pages, with notes paraphrasing those pages. Body and
  certification names are covered in [TRADEMARKS.md](TRADEMARKS.md).
- **Licence text:** the repository's `LICENSE` file is the
  GNU Affero General Public License v3 text, © Free Software Foundation,
  reproduced verbatim as that licence permits.

If you find something in this repository that should be listed here,
please open an issue.
