# Contributing to Vibeflow

Thank you for helping improve Vibeflow. The project values focused changes, deterministic evidence, and clear safety boundaries over feature volume.

## Before opening a change

1. Search existing issues and pull requests for related work.
2. Open an issue before a large architectural change so the direction can be agreed first.
3. Never include API keys, access tokens, private repository content, personal paths, or FCC configuration exports.
4. Keep FCC integration behind its local HTTP boundary. Do not copy or vendor FCC code into Vibeflow.

## Local development

```bash
git clone https://github.com/Duqamaqa/vibeflow.git
cd vibeflow
uv sync
```

Run the complete offline validation:

```bash
PYTHONPATH=src python3 -W error -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

Ordinary tests must use fakes or test doubles and must not make paid inference calls. Live FCC checks must remain optional or skip cleanly when the local gateway is unavailable.

## Pull requests

- Explain the user problem and the smallest complete solution.
- Include tests for behavior changes.
- Preserve unrelated dirty files and existing public interfaces unless the change explicitly requires otherwise.
- State the commands run and their exact results.
- Call out security, compatibility, migration, or provider-cost implications.
- Do not add automatic commit, push, merge, publish, or deployment behavior.

By contributing, you agree that your contribution is licensed under the repository's [MIT License](LICENSE).
