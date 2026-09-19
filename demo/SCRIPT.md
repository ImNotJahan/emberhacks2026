# Live demo: make Big Brother speak up

A 5-minute walkthrough. You write a small cart module with a classic Python bug, then "debug" it the way people do when they are stuck: tiny copy tweaks, undo, run again. Big Brother should **stay silent at the 3rd failure** and **speak at the 5th**. If it doesn't, it will speak during the final pause.

The bug is a mutable default argument (`cart=[]`). Every call shares one list, so the second test sees the first test's apple. None of the tweaks below touch that, so the failure never changes. That is exactly what the bot looks for.

Checked: `python -m judge.eval demo_cart --fresh` replays this script beat for beat. The trace is written by `demo_cart()` in [judge/scenarios.py](../judge/scenarios.py). In 6 of 6 runs it stayed silent at run 3 and spoke either at run 5 (5 runs) or in the final pause (1 run).

## Before you start

1. `pip install pytest`, if it isn't installed.
2. `recording_kit/save_session.sh` moves old traces aside, so an earlier session can't leak into this one.
3. Press **F5** in this repo to launch a fresh extension window. A fresh window matters: the bot remembers its own cooldowns.
4. In the new window, open this `demo/` folder. Open `test_cart.py` so the audience can see the tests.
5. In the terminal, `cd` into `demo/`. Delete `cart.py` if it's left over from a rehearsal.

## The script

Keep **at least 15 seconds between test runs**. Runs closer together than 10 seconds count as a single run.

### Beat 1: write the code (~1 min)

Create `cart.py` and **type** it. Don't paste it: typing is what the bot sees as normal work.

```python
def add_item(item, cart=[]):
    cart.append(item)
    return cart


def total(cart):
    return sum(price for _, price in cart)
```

Save, then run `pytest`. → **Run 1:** `1 failed, 2 passed`, with `AssertionError: assert [('apple', 1.5), ('pear', 2.0)] == [('pear', 2.0)]`.

*Say:* "Weird, there's an apple in my pear cart."

### Beat 2: two guesses, staying in cart.py (~40 s)

Stay in `cart.py`, and don't click over to the tests yet.

| Run | Edit (line 3) | Result |
|---|---|---|
| 2 | change `return cart` to `return list(cart)`, save, `pytest` | same failure |
| 3 | **Cmd+Z** back to `return cart`, save, `pytest` | same failure |

**Run 3: the bot stays silent.** *Say:* "It has noticed, but three tries is normal debugging, so it keeps quiet. That's the point."

### Beat 3: flail (~1 min)

Before each of the next edits, click over to `test_cart.py`, back to `cart.py`, to `test_cart.py` again, then back to `cart.py`, as if hunting for the problem.

| Run | Edit (line 3) | Result |
|---|---|---|
| 4 | `return cart[:]` | same failure |
| 5 | **Cmd+Z** back to `return cart` | same failure: **the bot should speak here** |
| 6 | `return cart.copy()` | same failure |
| 7 | **Cmd+Z** back to `return cart` | same failure |

If it speaks at run 5, stop there and read the nudge out. Skip runs 6–7 and go straight to Beat 5.

### Beat 4: hands off (fallback, up to ~2 min)

If it hasn't spoken yet, lean back and stop typing. *Say:* "This is what stuck looks like: seven identical failures, and now I'm staring." It speaks within about two minutes. It can't speak sooner because it waits three minutes before raising the same kind of problem again.

### Beat 5: the fix

```python
def add_item(item, cart=None):
    if cart is None:
        cart = []
    cart.append(item)
    return cart
```

Save and run `pytest`: `3 passed`. The bot stays quiet: going green is progress.

## If it doesn't speak

- **Runs too close together:** check the timing. Runs under 10 seconds apart count as one run.
- **Not a fresh window:** a nudge in the last 4 minutes blocks another one. Restart with F5.
- **No output reached the bot:** the terminal must have shell integration (the default in VS Code's zsh/bash terminals). Without it the extension never sees your commands.
- **Checking the capture side:** open the **Big Brother** channel in the Output panel. Also, `trace.candidates.jsonl` in this repo's root gets a line each time the engine hands a moment to the judge. Expect one around run 3 and one around run 5.
