# Creative Compare Agent

A fully local, deterministic MVP agent that compares two pieces of creative content and produces a structured scorecard. It works for text concepts, ad copy, campaign ideas, and asset descriptions without paid APIs, external services, or API keys.

The agent scores both options across:

- Clarity
- Originality
- Audience fit
- Emotional impact
- Differentiation
- CTA strength
- Risk / safety
- Overall recommendation

It supports JSON and Markdown output from a reusable Python class and a CLI entrypoint.

## Requirements

- Python 3.10+
- No runtime dependencies outside the Python standard library

## Quick start

```bash
cd /workspace/creative-compare-agent
python -m creative_compare_agent.cli \
  --a-file samples/concept_a.txt \
  --b-file samples/concept_b.txt \
  --audience "busy working parents who want healthy weeknight dinners" \
  --format markdown
```

JSON output:

```bash
python -m creative_compare_agent.cli \
  --input-json samples/comparison.json \
  --format json
```

Save output to a file:

```bash
python -m creative_compare_agent.cli \
  --input-json samples/comparison.json \
  --format markdown \
  --output scorecard.md
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

## CLI usage

```bash
python -m creative_compare_agent.cli --help
```

You can provide creative content in two ways:

1. Direct text or text files:

```bash
python -m creative_compare_agent.cli \
  --a "Try our app today and save time." \
  --b "Dinner sorted in 15 minutes for overloaded parents." \
  --audience "busy parents" \
  --format markdown
```

2. JSON input:

```json
{
  "creative_a": "Fresh meals delivered weekly with simple recipes.",
  "creative_b": "Win back your weeknights with 15-minute family dinners.",
  "name_a": "Meal Kit Concept",
  "name_b": "Weeknight Relief Concept",
  "audience": "busy working parents",
  "objective": "drive trial signups"
}
```

## How scoring works

This MVP uses deterministic heuristics, not a language model. Scores are explainable and repeatable. The agent uses signals such as sentence length, concrete details, audience keyword overlap, emotional language, CTA terms, cliché density, differentiation language, and safety/risk terms.

Scores are useful for fast first-pass review, creative team discussion, and consistent comparison. They are not a replacement for human judgment, brand review, legal review, or market testing.

## Python API

```python
from creative_compare_agent import CreativeCompareAgent

agent = CreativeCompareAgent()
scorecard = agent.compare(
    creative_a="Dinner sorted in 15 minutes for overloaded parents.",
    creative_b="Fresh meals delivered weekly with simple recipes.",
    audience="busy parents",
    objective="drive trial signups",
    name_a="Concept A",
    name_b="Concept B",
)

print(agent.to_markdown(scorecard))
print(agent.to_json(scorecard))
```

## Project layout

```text
creative-compare-agent/
├── README.md
├── pyproject.toml
├── creative_compare_agent/
│   ├── __init__.py
│   ├── __main__.py
│   ├── agent.py
│   └── cli.py
├── samples/
│   ├── comparison.json
│   ├── concept_a.txt
│   └── concept_b.txt
└── tests/
    ├── test_agent.py
    └── test_cli.py
```


## Web UI

A self-contained Flask web app lets you run the Master Creative Validation
Agent from the browser — fully local, no API keys, no external CDNs.

### Install

Flask is an optional dependency:

```bash
pip install flask
# or, via the extra:
pip install -e ".[web]"
```

### Launch

```bash
cd creative-compare-agent
python webapp/app.py
```

The server binds to `0.0.0.0` and listens on the port from the `PORT`
environment variable, defaulting to `5000`:

```bash
PORT=8080 python webapp/app.py   # -> http://localhost:8080
```

Then open http://localhost:5000 (or your chosen port).

### Using it

- Paste raw HTML/text into the **PTR (master)** and **Test Email** boxes,
  or upload a `.html` / `.txt` file for either (an uploaded file takes
  precedence over the pasted text).
- Click **Load sample** to pre-fill both boxes with `samples/ptr_email.html`
  and `samples/test_email.html` and try it in one click.
- Click **Run validation** to see a color-coded verdict banner, match score,
  counts by dimension and severity, and the full itemized variance table
  (critical = red, major = orange, minor = gray) with PTR vs Test values.
- Use **Download Markdown** / **Download JSON** to export the report.

The app reuses the existing validators (`creative_from_string` and
`MasterValidationAgent`) — it does not reimplement any validation logic.

## Deploy (public URL)

The app ships with everything needed to run on a real host: a WSGI
entrypoint (`wsgi.py` exposing `app`), a pinned `requirements.txt`,
a `Procfile`, a Render blueprint (`render.yaml`), and a `Dockerfile`.
Locally verified boot command:

```bash
gunicorn wsgi:app --bind 0.0.0.0:$PORT
```

### Render (recommended, free)

1. Push this repo to GitHub:
   ```bash
   git init && git add . && git commit -m "Deploy-ready"
   git branch -M main
   git remote add origin https://github.com/<you>/creative-compare-agent.git
   git push -u origin main
   ```
2. In the [Render dashboard](https://dashboard.render.com/), click
   **New +** → **Blueprint**.
3. Connect your GitHub account and **pick this repo**. Render
   auto-detects `render.yaml` and shows a free **web service**.
4. Click **Apply** / **Create**. Render runs
   `pip install -r requirements.txt` then starts
   `gunicorn wsgi:app --bind 0.0.0.0:$PORT`.
5. When the build finishes you get a permanent HTTPS URL like
   `https://creative-validation.onrender.com`. Open it — that's your
   public app.

> Free instances sleep after inactivity and cold-start on the next
> request (a few seconds). No credit card required.

### Railway (one-liner)

```bash
npm i -g @railway/cli && railway login && railway init && railway up
```
Railway detects the `Procfile` and gives you a public URL under
**Settings → Networking → Generate Domain**.

### Fly.io (one-liner)

```bash
curl -L https://fly.io/install.sh | sh && fly launch --now
```
`fly launch` detects the `Dockerfile`, builds it, deploys, and prints
your `https://<app>.fly.dev` URL. (Set the internal port to `8080` if
prompted.)

### Docker (any host)

```bash
docker build -t creative-validation .
docker run -p 8080:8080 -e PORT=8080 creative-validation
# → http://localhost:8080
```
Push the image to any registry (Docker Hub, GHCR, Fly, Cloud Run, etc.)
to deploy the same container anywhere.
