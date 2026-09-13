#!/usr/bin/env python3
"""The model seam: every generative call in this repo goes through here.

Ollama on localhost, running an open-weights model. This replaced the
Anthropic API on 2026-09-11, so generation now costs nothing and its only
dependency is this machine being awake.

Three Ollama behaviours this module exists to get right, because each of them
fails SILENTLY rather than loudly:

  Context window.  Ollama's default num_ctx is 4096 tokens. Every authoring
  prompt in this repo is bigger than that — the brand block plus the slide
  schema plus strategy.md plus the performance brief runs 10-20K — and the
  overflow is dropped from the FRONT of the conversation with no warning, so
  the model simply never sees its brand rules and you get plausible garbage.
  num_ctx is therefore always sent explicitly.

  Structured output.  `format` takes a JSON schema and constrains decoding to
  it, which is stricter than Anthropic's forced tool_choice was. It cannot be
  usefully combined with `tools` (the reply is JSON from the first token), so
  a step that must both search and return a shape runs as two calls: a tool
  loop, then a structured extraction. See generate_batch.author().

  Thinking.  qwen3 reasons before answering unless told not to. That is worth
  paying for when grading hooks and pure waste when emitting a long JSON
  document, so it is off unless a call site asks for it.

  But turning it off does not stop the model reasoning — it only removes
  somewhere to put the reasoning, and under constrained decoding the only
  place left is the first string field of the schema. "The user is asking me
  to write a strategy note based on the performance data provided..." was
  written into strategy.md that way, on every run of both accounts. A call
  whose answer needs real deliberation wants think=True; a call that is
  transcribing a shape it already has does not.

Environment overrides, all optional:
    OLLAMA_HOST          default http://localhost:11434
    OLLAMA_MODEL         default qwen3:30b-a3b
    OLLAMA_SCORER_MODEL  default = OLLAMA_MODEL
    OLLAMA_NUM_CTX       default 32768
    OLLAMA_TIMEOUT       default 1800 (seconds, per call)

    python llm.py            # check the server, the model and a round trip
"""
import json, os, re, sys, time, urllib.error, urllib.request

HOST = (os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
if "://" not in HOST:
    HOST = "http://" + HOST

# A 30B mixture-of-experts model that activates 3B parameters per token: it
# writes like a 30B and runs like a 3B, and its tool-calling and JSON
# adherence are what this pipeline leans on hardest.
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL") or "qwen3:30b-a3b"

# Hook grading used to run on a DIFFERENT, stronger model than authoring
# (Opus grading Sonnet), so the grader brought an outside opinion. With one
# local model that independence is gone and only the blindness survives —
# shuffled candidates, authorship hidden (see hooks.grade). Point this at a
# second pulled model to get the independence back.
SCORER_MODEL = os.environ.get("OLLAMA_SCORER_MODEL") or DEFAULT_MODEL

NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX") or 32768)
TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT") or 1800)

_THINK_TAGS = re.compile(r"<think>.*?</think>", re.S)


class LLMError(RuntimeError):
    """Anything that came back from Ollama that we cannot use."""


def _post(path, payload, timeout):
    req = urllib.request.Request(
        HOST + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(e, urllib.error.HTTPError):
            raise LLMError(f"Ollama returned {e.code}: "
                           f"{e.read().decode('utf-8', 'replace')[:400]}")
        raise LLMError(
            f"Cannot reach Ollama at {HOST} ({reason}). Start it with "
            f"`ollama serve`, or set OLLAMA_HOST if it lives elsewhere."
        ) from e


def available():
    """Models this server has pulled. Empty list if it is not running."""
    try:
        with urllib.request.urlopen(HOST + "/api/tags", timeout=10) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return []


def ensure(model=None):
    """Fail early and with the exact fix, rather than mid-batch."""
    model = model or DEFAULT_MODEL
    tags = available()
    if not tags:
        sys.exit(f"Ollama is not answering at {HOST}. Start it with `ollama serve`.")
    # `ollama list` shows qwen3:30b-a3b; a bare "qwen3" resolves to :latest.
    names = {t.split(":")[0] for t in tags} | set(tags)
    if model not in names and model.split(":")[0] not in names:
        sys.exit(f"Model {model!r} is not pulled. Run: ollama pull {model}")
    return model


def chat(messages, system=None, model=None, tools=None, schema=None,
         think=False, temperature=None, num_ctx=None, label="", timeout=None,
         max_tokens=None):
    """One /api/chat round trip. Returns Ollama's message dict.

    `schema` constrains decoding to a JSON schema; `tools` offers callable
    tools. Passing both is a mistake this refuses rather than silently
    resolves — see the module docstring.
    """
    if tools and schema:
        raise LLMError("tools and schema cannot be combined in one Ollama call; "
                       "run the tool loop first, then extract with a schema.")

    msgs = list(messages)
    if system:
        msgs = [{"role": "system", "content": system}] + msgs

    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": msgs,
        "stream": False,
        "think": bool(think),
        "options": {
            # num_predict -1 = no cap. The slide specs are long and a cap here
            # truncates mid-JSON, which reads as a model failure but is ours.
            # A caller that knows its answer is short should pass max_tokens:
            # asked to shorten one sentence, the model once generated 13,000
            # tokens of nothing over eighty seconds before giving up.
            "num_predict": max_tokens or -1,
            "num_ctx": num_ctx or NUM_CTX,
        },
    }
    if temperature is not None:
        payload["options"]["temperature"] = temperature
    if tools:
        payload["tools"] = list(tools)
    if schema:
        payload["format"] = schema

    started = time.time()
    resp = _post("/api/chat", payload, timeout or TIMEOUT)
    log_usage(resp, label or (model or DEFAULT_MODEL), time.time() - started)

    msg = resp.get("message") or {}
    msg["content"] = _THINK_TAGS.sub("", msg.get("content") or "").strip()
    # How full the window was for THIS call. tool_loop needs it to stop before
    # the conversation overflows; strip it before sending the message back.
    msg["_prompt_tokens"] = resp.get("prompt_eval_count") or 0
    msg["_num_ctx"] = payload["options"]["num_ctx"]
    if resp.get("done_reason") == "length":
        if max_tokens:
            raise LLMError(f"Answer ran past its {max_tokens}-token cap, so it "
                           f"is cut off. The caller asked for something short "
                           f"and got something long.")
        raise LLMError("Response hit the context limit before finishing. "
                       "Raise OLLAMA_NUM_CTX or ask for fewer posts at once.")
    return msg


def log_usage(resp, label, wall=None):
    """The local analogue of the old cache read/write line.

    What matters on a local model is not price, it is whether the prompt fit:
    prompt_eval_count creeping toward num_ctx is the early warning that the
    brand block is about to fall off the front of the window.
    """
    pin = resp.get("prompt_eval_count") or 0
    out = resp.get("eval_count") or 0
    if not (pin or out):
        return
    ctx = (resp.get("options") or {}).get("num_ctx") or NUM_CTX
    note = f"  [llm:{label}] in={pin} out={out} ctx={ctx}"
    if wall:
        note += f" {wall:.0f}s"
    if pin > 0.85 * ctx:
        note += "  ::warning:: prompt is near the context limit"
    print(note)


def _extract_json(text):
    """Parse a JSON object out of a reply, fence or no fence.

    `format` makes fences rare but not impossible, and a repair retry (below)
    answers in prose often enough to be worth handling.
    """
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0),
                default=-1)
    if start < 0:
        raise LLMError(f"no JSON in reply: {text[:200]!r}")
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start:end + 1])


def structured(system, user, schema, model=None, require=(), label="",
               think=False, temperature=None, attempts=3, messages=None,
               num_ctx=None, max_tokens=None):
    """Ask for one JSON document matching `schema`. Returns it parsed.

    Constrained decoding makes malformed JSON rare, but "valid JSON that is
    missing the field we need" still happens, so `require` names top-level
    keys that must be present and non-empty. A failure is re-asked with the
    problem quoted, which is cheap when the model is free and local.
    """
    base = list(messages) if messages else [{"role": "user", "content": user}]
    convo = list(base)
    last = None
    for attempt in range(1, attempts + 1):
        tag = label if attempt == 1 else f"{label}/retry{attempt - 1}"
        msg = chat(convo, system=system, model=model, schema=schema,
                   think=think, temperature=temperature, label=tag,
                   num_ctx=num_ctx, max_tokens=max_tokens)
        try:
            data = _extract_json(msg.get("content"))
            missing = [k for k in require
                       if k not in data or data[k] in (None, "", [], {})]
            if missing:
                raise LLMError(f"missing or empty: {', '.join(missing)}")
            return data
        except (LLMError, json.JSONDecodeError, ValueError) as e:
            last = e
            print(f"  ::warning::{label or 'structured'} attempt {attempt} "
                  f"unusable ({e}) — re-asking")
            convo = base + [
                {"role": "assistant", "content": (msg.get("content") or "")[:2000]},
                {"role": "user", "content":
                    f"That reply was unusable: {e}. Return the complete JSON "
                    f"document again, matching the schema exactly. Output JSON "
                    f"only, no commentary."},
            ]
    raise LLMError(f"{label or 'structured'} failed after {attempts} attempts: {last}")


def tool_loop(system, user, tools, dispatch, model=None, max_rounds=10,
              label="tools", think=False, num_ctx=None):
    """Let the model call local tools until it stops asking. Returns the
    finished message list, so a follow-up structured() call can read what the
    tools actually returned.

    `dispatch(name, args)` runs one tool and returns a string. It must not
    raise: a search that fails is worth reporting to the model, not worth
    losing the run over.

    The loop stops on whichever comes first: the model stops asking, the round
    limit, or the conversation filling the context window. That last one is
    the important stop. Every page read adds thousands of tokens, so a search
    loop grows fast, and overflowing does not raise — Ollama drops the oldest
    turns, which here means the instructions. Caller gets what was gathered up
    to that point, which is worth far more than a truncated run.
    """
    convo = [{"role": "user", "content": user}]
    calls = 0
    for rnd in range(max_rounds):
        msg = chat(convo, system=system, model=model, tools=tools,
                   think=think, label=f"{label}:{rnd + 1}", num_ctx=num_ctx)
        convo.append({k: v for k, v in msg.items() if k in
                      ("role", "content", "tool_calls")})
        requested = msg.get("tool_calls") or []
        if not requested:
            break
        for call in requested:
            fn = (call.get("function") or {})
            name = fn.get("name", "")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            try:
                result = dispatch(name, args)
            except Exception as e:                      # noqa: BLE001
                result = f"tool {name} failed: {e}"
            calls += 1
            convo.append({"role": "tool", "tool_name": name,
                          "content": str(result)[:12000]})
        # Checked here, after the tool results are in, so the conversation is
        # never left holding a tool call nobody answered. 0.7 leaves room for
        # the structured extraction that normally follows this loop.
        ceiling = 0.7 * (msg.get("_num_ctx") or num_ctx or NUM_CTX)
        if msg.get("_prompt_tokens", 0) > ceiling:
            print(f"  ::warning::{label} stopping after round {rnd + 1} — the "
                  f"conversation is filling the context window. Working with "
                  f"what it found so far.")
            break
    else:
        print(f"  ::warning::{label} hit {max_rounds} rounds without settling")
    return convo, calls


def main():
    print(f"host    {HOST}")
    print(f"model   {DEFAULT_MODEL}")
    print(f"scorer  {SCORER_MODEL}")
    print(f"num_ctx {NUM_CTX}")
    tags = available()
    if not tags:
        sys.exit(f"Ollama is not answering at {HOST}. Start it with `ollama serve`.")
    print(f"pulled  {', '.join(tags)}")
    ensure()
    out = structured(
        "You answer with JSON only.",
        "Reply with {\"ok\": true, \"model\": \"<your model family>\"}.",
        {"type": "object",
         "properties": {"ok": {"type": "boolean"}, "model": {"type": "string"}},
         "required": ["ok", "model"]},
        require=("ok",), label="selftest")
    print(f"round trip ok: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
