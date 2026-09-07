# Setting up the terminal on your Mac

One of two places the benchmarks can be computed; the CodeOSS cloud instance is
the other, and is usually easier because it is already set up and reachable from
a browser on any device. Use this if you would rather the pulls run on hardware
you own — a full benchmark run is an hour or two of downloading, and the Mac is
always on.

Either way it must be a machine with open outbound HTTPS and a real desktop
Python. An Android phone is not one: `pandas`, `pyarrow` and `pyreadr` are
compiled extensions with no Android builds.

About 15 minutes, most of it waiting on downloads. You only do it once.

---

## 1. Open Terminal

`Cmd + Space`, type `Terminal`, press Return. A window opens with a prompt that
ends in `%`. Everything below is typed there, one line at a time, pressing
Return after each.

Two things worth knowing before you start:

* **Copy-paste works.** `Cmd + V` pastes. You do not have to type any of this.
* **A command that prints nothing usually worked.** Unix tools are quiet on
  success. Errors are loud.

---

## 2. Install the developer tools

macOS ships without `git`. Ask for it and macOS offers to install it:

```bash
xcode-select --install
```

A dialog appears — click **Install** and wait (a few minutes). If it says
"command line tools are already installed", you already have them; move on.

Check it worked:

```bash
git --version
```

You want something like `git version 2.39.5`. Any version is fine.

---

## 3. Install Python 3.12

macOS's built-in Python is 3.9, which is too old — this code uses syntax 3.9
rejects. Check what you have:

```bash
python3 --version
```

**If it says 3.11 or higher, skip to step 4.** Otherwise:

1. Go to [python.org/downloads/macos](https://www.python.org/downloads/macos/)
2. Download the **macOS 64-bit universal2 installer** for Python **3.12**
3. Open the `.pkg` and click through it

(3.12 rather than the newest: every dependency here publishes a ready-built
Apple Silicon wheel for it, so nothing has to compile on your machine.)

Then **close Terminal and open a new one** — it does not notice a new Python
otherwise — and check:

```bash
python3 --version
```

You want `Python 3.12.x`.

---

## 4. Get the code

```bash
cd ~
git clone https://github.com/smeredith15/whul.git
cd whul
git checkout claude/fantasy-league-webapp-dp99e3
```

That last line matters: the work is on that branch, not on `main`. Confirm:

```bash
git branch --show-current
```

It should print `claude/fantasy-league-webapp-dp99e3`.

**Coming back to this later?** Don't clone again — it will refuse. Refresh
instead:

```bash
cd ~/whul
git checkout claude/fantasy-league-webapp-dp99e3
git pull
```

---

## 5. Get the tennis history

`model_data_snapshot.rds` is the only surviving copy of the match history — the
Sackmann archive it came from was taken down. It lives in your other repo, and
it needs to sit *next to* this one:

```bash
cd ~
git clone https://github.com/smeredith15/tennis2026.git
```

You should now have both `~/whul` and `~/tennis2026`. Nothing else to
configure — the tennis loader looks for a sibling checkout by default.

---

## 6. Build the environment

```bash
cd ~/whul
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
```

The quotes around `'.[dev]'` are load-bearing — without them the shell tries to
expand the brackets as a filename pattern and the install fails.

That takes 1-3 minutes (pyarrow is a large download) and should end with
`Successfully installed ... whul-0.1.0`.

**What `.venv` is:** a private Python just for this project, so installing
things here cannot break anything else on your Mac. It is why every command
below starts with `.venv/bin/python` instead of plain `python3` — that is how
you say "the project's Python, with the project's packages".

---

## 7. Check it works

```bash
.venv/bin/python -m pytest -q
```

About a minute. You want it to end in something like `702 passed`. If anything
fails, stop here and send me the output — do not compute benchmarks against a
broken checkout.

---

## 8. Build the database

The database is not in the repository (it is a build artifact, and it would
churn on every commit). Build it from the draft spreadsheet, which *is*:

```bash
.venv/bin/python -m whul.cli import-rosters
```

That is a dry run — it reads the sheet, prints how it mapped the columns, and
writes nothing. Check the numbers look right (205 picks, 5 managers, 95 slots
still open), then commit it:

```bash
.venv/bin/python -m whul.cli import-rosters --write
```

You now have `data/whul.sqlite3`.

---

## You're set up

Everything is in place. The benchmark procedure is
[`docs/BENCHMARKS.md`](BENCHMARKS.md); the first real command is:

```bash
.venv/bin/python -m whul.cli benchmarks list
```

---

## If something goes wrong

**`command not found: python3`** — the developer tools did not finish
installing. Re-run `xcode-select --install`.

**`zsh: no matches found: .[dev]`** — the quotes got lost. It must be
`.venv/bin/pip install -e '.[dev]'`.

**`error: externally-managed-environment`** — you are installing into the
system Python instead of the project's. Make sure you ran `python3 -m venv
.venv` first, and that the command starts with `.venv/bin/pip`, not `pip3`.

**A `benchmarks compute` that fails with `ProxyError` or `403`** — you are on
the cloud instance, not the Mac. Check with `hostname`.

**`FileNotFoundError` mentioning `model_data_snapshot.rds`** — the tennis
checkout is missing or somewhere else. Either clone it next to `~/whul` as in
step 5, or point at it:

```bash
export WHUL_TENNIS2026=/wherever/you/put/tennis2026
```

(That lasts until you close the Terminal window. To make it permanent, add the
same line to `~/.zshrc`.)
