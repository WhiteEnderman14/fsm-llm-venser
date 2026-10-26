# fsm-llm-venser

Finite-state machines written in YAML, with nested machines, driven by an LLM.

You describe a conversation as states, events and transitions. The engine runs it: the model turns
what a person says into one of the events the current state accepts and writes the next question,
while the transitions stay deterministic. Anything that needs Python (guards, actions, listeners)
goes in a separate plugin file; a machine file never runs code.

Two examples live in `examples/`; the engine in `src/` knows nothing about either of them:

- `narrative_medicine_interview/` is the default one: the narrative-medicine interview from the
  course notebook, with the emotions stage written as a nested machine. The commands below use it.
- `support_ticket/` is a support-desk intake that uses every feature of the format once or twice,
  with a comment on each. Read it when you want to see a feature at work.

- [Install and run](#install-and-run): on your computer, or in containers
- [Commands](#commands)
- [Write your own machine](#write-your-own-machine)
- [How it works](#how-it-works) and [layout](#layout)
- [Problems](#problems)

## Install and run

There are two ways.

| | Local | Containers |
|---|---|---|
| You install | `git`, `uv`, Ollama, optionally Graphviz | Docker or Podman, `git` |
| Good for | developing, editing machines | not touching the system, same result everywhere |
| GPU | Ollama finds it by itself | needs a few extra steps, see [GPU](#gpu) |

Commands below work in Linux, macOS and Windows (PowerShell) unless a line says otherwise. They are
written on one line, with paths using `/`, which Windows accepts too.

### Local

**1. Install the tools.** `uv` installs Python and the dependencies by itself, so you do not need
to install Python.

| | git | uv | Ollama | Graphviz (optional, to draw) |
|---|---|---|---|---|
| Arch, Manjaro | `sudo pacman -S git` | `sudo pacman -S uv` | `sudo pacman -S ollama` | `sudo pacman -S graphviz` |
| Debian, Ubuntu | `sudo apt install git` | script below | script below | `sudo apt install graphviz` |
| Fedora | `sudo dnf install git` | script below | script below | `sudo dnf install graphviz` |
| openSUSE | `sudo zypper install git` | script below | script below | `sudo zypper install graphviz` |
| macOS (Homebrew) | `brew install git` | `brew install uv` | `brew install ollama` | `brew install graphviz` |
| Windows | `winget install Git.Git` | `winget install astral-sh.uv` | `winget install Ollama.Ollama` | `winget install Graphviz.Graphviz` |

The scripts, for the distros where the package manager has no recent version:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh          # uv, Linux and macOS
curl -fsSL https://ollama.com/install.sh | sh            # Ollama, Linux
```

On Windows, `winget` is part of Windows 10 and 11. The uv installer also exists for PowerShell:
`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`. After any
install on Windows, open a new terminal so the new programs are on the PATH. The Graphviz installer
asks whether to add itself to the PATH; say yes, or `draw` will not find it.

**2. Get the project and install it.**

```bash
git clone https://github.com/WhiteEnderman14/fsm-llm-venser.git
cd fsm-llm-venser
uv sync --extra ollama
```

**3. Start the model server and download the model** (once):

| | Start Ollama |
|---|---|
| Linux with systemd | `sudo systemctl enable --now ollama` |
| macOS | `brew services start ollama`, or open the Ollama app |
| Windows | the installer starts it; the Ollama icon in the tray means it is running |
| anywhere | `ollama serve` in a spare terminal also works |

```bash
ollama pull llama3.2
uv run fsm-llm-venser check-llm
```

`check-llm` should print a short answer from the model. If it does not, see [Problems](#problems).

**4. Try the example.**

```bash
uv run fsm-llm-venser validate examples/narrative_medicine_interview/machine.yaml --plugin examples/narrative_medicine_interview/plugin.py
uv run fsm-llm-venser run-word examples/narrative_medicine_interview/machine.yaml --plugin examples/narrative_medicine_interview/plugin.py CONFIRM ANSWER
uv run fsm-llm-venser run examples/narrative_medicine_interview/machine.yaml --plugin examples/narrative_medicine_interview/plugin.py --scenario examples/narrative_medicine_interview/scenarios/a_happy_path.yaml --draw path.svg
uv run fsm-llm-venser run examples/narrative_medicine_interview/machine.yaml --plugin examples/narrative_medicine_interview/plugin.py --interactive
```

In order: check the file (no model), walk a path by feeding events (no model), a full run with
scripted replies and the path drawn in red, and a full run where you type the replies.

### Models

The default is `llama3.2` on Ollama, and it is the only model we tested. The engine does not
depend on it: it needs an object with two methods, `classify` (a structured answer) and `write`
(free text). Providers build that object, three come with the code and you can add more.

| Provider | Install | Needs |
|---|---|---|
| `ollama` (default) | `uv sync --extra ollama` | a running Ollama server. Any model you pulled works, for example the Qwen family |
| `gemini` (Google) | `uv sync --extra gemini` | `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) in the environment |
| `claude` (Anthropic) | `uv sync --extra claude` | `ANTHROPIC_API_KEY` in the environment |

Choose with `--provider` and `--model`. To try Qwen on your own machine:

```bash
ollama pull qwen3:8b
uv run fsm-llm-venser check-llm --model qwen3:8b
uv run fsm-llm-venser run examples/narrative_medicine_interview/machine.yaml --plugin examples/narrative_medicine_interview/plugin.py --interactive --model qwen3:8b
```

`uv run fsm-llm-venser models` lists the known names, each flagged `default` or `untested`.
`--model` accepts any name the provider knows, listed or not.

To make a model work in most cases the client is forgiving. It asks for structured output and
tries once more if that fails. If the model cannot do structured output at all, it asks for plain
JSON and parses it, also when the answer comes in a code fence or after a `<think>` block (Qwen 3,
DeepSeek and other reasoning models). An event written in lower case, in quotes or followed by
an explanation is still understood. If nothing works, the state's `on_invalid` event is used, so a
run never crashes because of the model. Small models still make more mistakes than big ones: you
will see more `[guard]` lines.

Another LLM does not need a change in the engine: a plugin file can register a provider, and then
`--provider` accepts it. This is explained in [docs/plugins.md](docs/plugins.md#adding-an-llm-provider).
In containers the same options work, see "Models in containers" below.

### Containers

Everything runs in containers, Ollama included. You only need the container engine and `git`.

| | Install |
|---|---|
| Linux, Docker | Docker Engine and the compose plugin: `sudo pacman -S docker docker-compose` on Arch, or the [official guide](https://docs.docker.com/engine/install/) for Debian, Ubuntu, Fedora. Then `sudo systemctl enable --now docker` and add yourself to the `docker` group. |
| Linux, Podman | `sudo pacman -S podman podman-compose`, `sudo apt install podman podman-compose`, or `sudo dnf install podman podman-compose` |
| macOS | Docker Desktop, or `brew install podman podman-compose` then `podman machine init` and `podman machine start` |
| Windows | Docker Desktop (with the WSL 2 backend), or Podman Desktop |

Below, `docker compose` can be replaced by `podman compose` everywhere. Clone the project as in
the local steps, `cd` into it, then:

```bash
docker compose build
docker compose up -d ollama
docker compose exec ollama ollama pull llama3.2
docker compose run --rm fsm check-llm
docker compose run --rm fsm validate examples/narrative_medicine_interview/machine.yaml --plugin examples/narrative_medicine_interview/plugin.py
docker compose run --rm fsm run examples/narrative_medicine_interview/machine.yaml --plugin examples/narrative_medicine_interview/plugin.py --scenario examples/narrative_medicine_interview/scenarios/a_happy_path.yaml --draw /out/path.svg
docker compose run --rm fsm run examples/narrative_medicine_interview/machine.yaml --plugin examples/narrative_medicine_interview/plugin.py --interactive
docker compose down
```

The `fsm` image holds the engine and the example. `./examples` is mounted into it, so your own
machines are visible without rebuilding, and drawings written to `/out` end up in `./out`. The
downloaded model lives in a volume and survives `down`.

#### Models in containers

- **Other Ollama models** (Qwen and so on) are pulled into the same volume:
  `docker compose exec ollama ollama pull qwen3:8b`, then
  `docker compose run --rm fsm check-llm --model qwen3:8b`.
- **Gemini and Claude** need two things the default setup does not have: their Python packages in
  the image and the API key in the container. Both go in a `.env` file next to `compose.yaml`
  (git ignores it, and it never enters the image):

```bash
EXTRAS=ollama gemini claude
GOOGLE_API_KEY=your-key
ANTHROPIC_API_KEY=your-key
```

  Rebuild once with `docker compose build`, then
  `docker compose run --no-deps --rm fsm check-llm --provider gemini`. The `--no-deps` skips the
  Ollama container, which a cloud model does not need.
- **A provider added by a plugin** works too, since `./examples` is mounted. Its package has to be
  in the image: add it to `EXTRAS` if it is an extra of this project, otherwise to your own
  Dockerfile. Inside a container `localhost` is the container itself, so a server on your computer
  is `host.docker.internal` (Podman: `host.containers.internal`), as in the Ollama note below.

Things that differ between systems:

- **SELinux** (Fedora, RHEL): the `:Z` on the volumes in `compose.yaml` is what makes them readable.
  If your engine rejects it (some Docker Desktop setups do), delete the `,Z` / `:Z` parts.
- **Ollama on the host instead of in a container** (macOS, or Windows with the Ollama app, where
  the container would only use the CPU): drop the `ollama` service and set
  `OLLAMA_HOST=http://host.docker.internal:11434` for `fsm` (Podman:
  `http://host.containers.internal:11434`). On Linux with Docker add
  `extra_hosts: ["host.docker.internal:host-gateway"]`.
- **Without compose**, plain commands:

```bash
docker network create fsm
docker run -d --name ollama --network fsm -v ollama:/root/.ollama docker.io/ollama/ollama
docker exec ollama ollama pull llama3.2
docker build -t fsm-llm-venser .
docker run --rm -it --network fsm -e OLLAMA_HOST=http://ollama:11434 fsm-llm-venser check-llm
```

#### GPU

Without a GPU Ollama runs on the CPU. It works, but a full interview takes minutes. To use the
GPU, the host needs a driver and a container hook, then an override file adds the device.

| Case | Host setup | Override file |
|---|---|---|
| NVIDIA, Docker, Linux | driver + NVIDIA container toolkit, then `sudo nvidia-ctk runtime configure --runtime=docker` and restart Docker | `compose.nvidia.yaml` |
| NVIDIA, Podman, Linux | driver + NVIDIA container toolkit, then `sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml` | `compose.nvidia-podman.yaml` |
| NVIDIA, Docker Desktop on Windows | recent NVIDIA driver on Windows, WSL 2 backend, nothing else | `compose.nvidia.yaml` |
| AMD, Docker or Podman, Linux | `amdgpu` driver | `compose.amd.yaml` |
| macOS | containers cannot use the GPU: run Ollama natively (see above) | none |

The toolkit is `sudo pacman -S nvidia-container-toolkit` on Arch. On Debian, Ubuntu and Fedora it
comes from NVIDIA's own repository, see the
[install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

Every compose command (`up`, `run`, `exec`, `down`) has to see the override file, or the container
is recreated without the GPU. The simplest way is to copy it to `compose.override.yaml`, which
compose reads on its own and git ignores:

```bash
cp compose.nvidia-podman.yaml compose.override.yaml        # Windows: copy ... ; Docker: compose.nvidia.yaml
docker compose up -d ollama
```

Or pass the files by hand each time: `docker compose -f compose.yaml -f compose.nvidia.yaml up -d ollama`.
Setting `COMPOSE_FILE` in the shell works as well, but Podman does not read it from `.env`.

Check it: `docker compose exec ollama nvidia-smi -L` must list the card (NVIDIA only), and after a
run `docker compose exec ollama ollama ps` must say `100% GPU`. If it says `CPU`, run
`docker compose down` and `docker compose up -d ollama` again with the override in place.

## Commands

All of them are `fsm-llm-venser COMMAND ...` (with `uv run` in front when installed locally, or
`docker compose run --rm fsm` in containers).

| Command | What it does |
|---|---|
| `validate FILE [--plugin P]` | checks the file and prints a summary |
| `table FILE` | prints the compiled table: for each state its arrows, fallbacks and `on_invalid` |
| `draw FILE [-o OUT]` | writes the diagram as SVG, one box per nested machine (needs Graphviz) |
| `run-word FILE EVENT... [--plugin P]` | runs a sequence of events, no model |
| `run FILE --plugin P (--scenario S \| --interactive)` | runs with a model. Options: `--provider`, `--model`, `--temperature`, `--max-turns`, `--draw SVG` (draws the path of the run in red) |
| `check-llm [--provider P] [--model M] [--plugin P]` | one tiny request to see if the model answers |
| `models [--plugin P]` | lists the known models, including providers added by a plugin |
| `--version` | prints the version |

Exit codes: `0` ok, `1` wrong input (file, plugin, options), `3` the run did not end in a final state.

A scenario file is a list of scripted replies, handy to repeat the same run:

```yaml
name: A happy path
replies:
  - 'Yes, go ahead.'
  - '...'
when_exhausted: 'I would rather stop here.'     # reply once the list is used up
```

While a run goes, every turn prints the question, the reply and a line like
`[llm] OPENING --ANSWER--> TIMELINE`. The word in brackets says who chose the event: `rule` (a
regular expression), `llm` (the model), `guard` (the model picked an event the state does not
accept, so `on_invalid` replaced it) or `error` (the model failed).

## Write your own machine

1. Copy `examples/narrative_medicine_interview/` to `examples/my_machine/` and edit it, or start
   from the smallest machine in [docs/file-format.md](docs/file-format.md#the-smallest-machine).
2. Run `validate` after every change. It lists all the problems at once.
3. Walk the paths with `run-word`, then try the model with `run --interactive`.
4. If you need code (keep data between turns, build a result, custom guards), write a plugin.

| Page | Content |
|---|---|
| [docs/file-format.md](docs/file-format.md) | the machine file, key by key: events, rules, states, transitions, guards, nested machines, finals, prompts, and how a turn runs |
| [docs/plugins.md](docs/plugins.md) | writing guards, actions and listeners, the variables and `ctx`, and the guards that come with the project (`when`, `reply_matches`) |

## How it works

1. `machine/schema.py` reads the YAML with Pydantic (structure only).
2. `machine/compiler.py` compiles the machine into a flat table: nested machines become states with
   qualified names (`EMOTIONS.INVITE`), `from_any_state` rules are copied into every state they
   cover, and each move records which states it exits and enters.
3. `engine/graph.py` builds a LangGraph graph with one node per state. A node plays one turn
   (`engine/runner.py`): the machine asks, the person replies, the classifier turns the reply into
   an event (`llm/classifier.py`: rules, then the model, then a guard on its answer), and the
   transition runs its actions.
4. When a final state is reached the run ends; the result holds the transcript and the variables.

The model only chooses among the events the current state accepts. If it fails, the `on_invalid`
event of the state is used; after `max_retries` turns in the same state the `fallback` event takes
over, so a run always ends.

## Layout

```
Dockerfile, compose*.yaml  containers (Docker or Podman); the nvidia and amd files add a GPU
src/fsm_llm_venser/    the engine, the same for every machine
    fsm_cli.py          the command line
    machine/            read and compile the YAML (loader, templates, schema, compiler, model)
    plugins/            the Plugin class and the ready-made guards
    engine/             run a machine (runner, LangGraph graph, context, trace)
    llm/                classify replies, write questions, providers
    tools/              draw the machine, sources of replies (scenario file, keyboard)
examples/              specific machines, each with machine.yaml, plugin.py and scenarios/
    narrative_medicine_interview/   the default example: the course interview
    support_ticket/     every feature of the format, one at a time
notebooks/             Colab launcher
docs/                  file-format.md, plugins.md
```

## Problems

| Symptom | What to try |
|---|---|
| `the model did not answer: ...` | Ollama is not running. Start it (see step 3). In containers: `docker compose ps`, and is `OLLAMA_HOST` set to `http://ollama:11434`? |
| the error mentions a missing model | `ollama pull llama3.2` (in containers: `docker compose exec ollama ollama pull llama3.2`) |
| `Graphviz is not installed` | Graphviz is not installed or not on the PATH. In containers it is already in the image. |
| `uv: command not found` right after installing | open a new terminal |
| Many `[guard]` or `[error]` lines with a small model | try a bigger one (`--model qwen3:8b`), lower `--temperature`, and give the events clearer descriptions and `examples` |
| Very slow answers | probably the CPU. Check with `ollama ps` (the PROCESSOR column) and see [GPU](#gpu). |
| `[guard]` lines in a run | the model picked an event the state does not accept. A few are normal with a small model; if many, improve the event descriptions or add `examples`. |
| `permission denied` on the Docker socket | add your user to the `docker` group and log in again, or use Podman |
| Windows: `winget` not found | update "App Installer" from the Microsoft Store, or download the installers by hand |

## Colab

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/WhiteEnderman14/fsm-llm-venser/blob/main/notebooks/colab_launcher.ipynb)

The notebook `notebooks/colab_launcher.ipynb` installs the package from GitHub, installs Ollama
(or reads a Gemini or Claude key from Colab Secrets) and runs the example.

## Authors

Massimo Ventura and Sonia Sergio.

## Note

The interview and its keyword rules are an illustration. They are not clinically validated.
