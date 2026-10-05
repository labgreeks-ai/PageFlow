# Contributing to PageFlow

Thanks for wanting to help! Bug reports, ideas and pull requests are all welcome.

## Reporting a problem

Open an issue and say:
- what you did and what you expected to happen
- your Windows (or other) version and how you run PageFlow (the exe or from source)
- if it is about one PDF, whether the problem happens with other PDFs too (please do not attach private documents)

## Working on the code

```
git clone https://github.com/labgreeks-ai/PageFlow.git
cd PageFlow
pip install -r requirements.txt
python pageflow.py
```

Run the tests before sending a change. They run without a screen:

```
python tests/test_reader.py
python tests/test_selection.py
```

(On Linux and macOS set `QT_QPA_PLATFORM=offscreen` first if you have no display.)

Guidelines:
- Keep it simple and readable; PageFlow is meant to stay small and quick.
- Add a test when you fix a bug or add a feature.
- Update `CHANGELOG.md` for changes people will notice.

## Licence

By contributing you agree that your contribution is released under the project's licence, the GNU AGPL-3.0 (see `LICENSE`).
