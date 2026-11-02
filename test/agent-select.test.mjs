import test from "node:test";
import assert from "node:assert/strict";
import { PassThrough } from "node:stream";
import { HARNESS_AGENTS } from "../lib/paths.mjs";
import {
  shouldPromptInstall,
  createSelectionState,
  normalizeSelectionKey,
  reduceSelection,
  selectAgents,
} from "../lib/agent-select.mjs";

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

function fakeTTY({ raw = false, withSetRawMode = true } = {}) {
  const input = new PassThrough();
  input.isTTY = true;
  input.isRaw = raw;
  input.rawCalls = [];
  if (withSetRawMode) {
    input.setRawMode = (value) => {
      input.rawCalls.push(value);
      input.isRaw = value;
    };
  }
  return input;
}

function fakeOutput() {
  return {
    isTTY: true,
    writes: [],
    write(chunk) {
      this.writes.push(String(chunk));
      return true;
    },
    text() {
      return this.writes.join("");
    },
  };
}

const KEY = {
  up: ["\u001b[A", { name: "up" }],
  down: ["\u001b[B", { name: "down" }],
  space: [" ", { name: "space" }],
  a: ["a", { name: "a" }],
  shiftA: ["A", { name: "a", shift: true }],
  enter: ["\r", { name: "return" }],
  newline: ["\n", { name: "enter" }],
  q: ["q", { name: "q" }],
  shiftQ: ["Q", { name: "q", shift: true }],
  ctrlC: ["\u0003", { name: "c", ctrl: true }],
  ctrlA: ["\u0001", { name: "a", ctrl: true }],
  ctrlQ: ["\u0011", { name: "q", ctrl: true }],
  tab: ["\t", { name: "tab" }],
  escape: ["\u001b", { name: "escape" }],
};

function press(input, name) {
  input.write(KEY[name][0]);
}

// --- AC1: prompt conditions ---

test("shouldPromptInstall is true only for bare install with three TTY streams", () => {
  const tty = { isTTY: true };
  const pipe = { isTTY: false };
  const base = {
    command: "install",
    agentArg: undefined,
    jsonMode: false,
    stdin: tty,
    stdout: tty,
    stderr: tty,
  };

  assert.equal(shouldPromptInstall(base), true);
  assert.equal(shouldPromptInstall({ ...base, agentArg: "" }), false, "explicit empty --agent counts as given");
  assert.equal(shouldPromptInstall({ ...base, agentArg: "codex" }), false);
  assert.equal(shouldPromptInstall({ ...base, agentArg: "all" }), false);
  assert.equal(shouldPromptInstall({ ...base, jsonMode: true }), false);

  for (const command of ["doctor", "uninstall", "version", "help", "unknown"]) {
    assert.equal(shouldPromptInstall({ ...base, command }), false, command);
  }

  for (const stream of ["stdin", "stdout", "stderr"]) {
    assert.equal(shouldPromptInstall({ ...base, [stream]: pipe }), false, `${stream} piped`);
    assert.equal(shouldPromptInstall({ ...base, [stream]: undefined }), false, `${stream} missing`);
    assert.equal(shouldPromptInstall({ ...base, [stream]: {} }), false, `${stream} without isTTY`);
  }
});

// --- AC2/AC3/AC4/AC5: pure state and reducer ---

test("createSelectionState starts at cursor 0 with only claude-code selected", () => {
  const state = createSelectionState(HARNESS_AGENTS);
  assert.equal(state.cursor, 0);
  assert.deepEqual([...state.selected], ["claude-code"]);
  assert.equal(state.message, null);
});

test("createSelectionState keeps only defaults that exist and ignores unknown names", () => {
  const state = createSelectionState(HARNESS_AGENTS, ["codex", "agents"]);
  assert.deepEqual([...state.selected], ["codex"]);
});

test("createSelectionState rejects invalid agent lists", () => {
  assert.throws(() => createSelectionState([]), /agent/i);
  assert.throws(() => createSelectionState(["codex", "codex"]), /agent/i);
  assert.throws(() => createSelectionState(["codex", ""]), /agent/i);
  assert.throws(() => createSelectionState("codex"), /agent/i);
});

test("normalizeSelectionKey maps the keyboard contract and ignores everything else", () => {
  const cases = [
    ["up", "up"],
    ["down", "down"],
    ["space", "toggle"],
    ["a", "toggle-all"],
    ["shiftA", "toggle-all"],
    ["enter", "confirm"],
    ["newline", "confirm"],
    ["q", "cancel"],
    ["shiftQ", "cancel"],
    ["ctrlC", "cancel"],
    ["ctrlA", null],
    ["ctrlQ", null],
    ["tab", null],
    ["escape", null],
  ];
  for (const [name, expected] of cases) {
    const [str, key] = KEY[name];
    assert.equal(normalizeSelectionKey(str, key), expected, name);
  }
  assert.equal(normalizeSelectionKey(undefined, undefined), null);
  assert.equal(normalizeSelectionKey("x", { name: "x" }), null);
});

test("reduceSelection moves with wrapping in both directions", () => {
  const start = createSelectionState(HARNESS_AGENTS);
  const up = reduceSelection(start, HARNESS_AGENTS, "up");
  assert.equal(up.state.cursor, HARNESS_AGENTS.length - 1);
  const down = reduceSelection(start, HARNESS_AGENTS, "down");
  assert.equal(down.state.cursor, 1);
  const last = createSelectionState(HARNESS_AGENTS);
  last.cursor = HARNESS_AGENTS.length - 1;
  assert.equal(reduceSelection(last, HARNESS_AGENTS, "down").state.cursor, 0);
  assert.equal(start.cursor, 0, "reducer must not mutate its input");
});

test("reduceSelection toggles the cursor item and later confirms in canonical order", () => {
  let state = createSelectionState(HARNESS_AGENTS);
  state = reduceSelection(state, HARNESS_AGENTS, "down").state;
  state = reduceSelection(state, HARNESS_AGENTS, "toggle").state;
  assert.deepEqual([...state.selected].sort(), ["claude-code", "codex"]);

  state.cursor = HARNESS_AGENTS.indexOf("pi");
  state = reduceSelection(state, HARNESS_AGENTS, "toggle").state;
  const confirm = reduceSelection(state, HARNESS_AGENTS, "confirm");
  assert.deepEqual(confirm.result, ["claude-code", "codex", "pi"]);

  const untoggled = reduceSelection(state, HARNESS_AGENTS, "toggle").state;
  assert.equal(untoggled.selected.has("pi"), false, "second toggle deselects");
});

test("reduceSelection 'a' toggles all or none", () => {
  const start = createSelectionState(HARNESS_AGENTS);
  const all = reduceSelection(start, HARNESS_AGENTS, "toggle-all").state;
  assert.equal(all.selected.size, HARNESS_AGENTS.length);
  const none = reduceSelection(all, HARNESS_AGENTS, "toggle-all").state;
  assert.equal(none.selected.size, 0);
});

test("reduceSelection rejects an empty confirm and clears the message on the next action", () => {
  const allState = reduceSelection(createSelectionState(HARNESS_AGENTS), HARNESS_AGENTS, "toggle-all").state;
  const emptyState = reduceSelection(allState, HARNESS_AGENTS, "toggle-all").state;
  const empty = reduceSelection(emptyState, HARNESS_AGENTS, "confirm");
  assert.equal(empty.result, undefined);
  assert.equal(empty.state.message, "至少选择一个 agent。");
  const cleared = reduceSelection(empty.state, HARNESS_AGENTS, "down").state;
  assert.equal(cleared.message, null);
});

test("reduceSelection cancel resolves null", () => {
  const cancel = reduceSelection(createSelectionState(HARNESS_AGENTS), HARNESS_AGENTS, "cancel");
  assert.equal(cancel.result, null);
});

// --- AC2/AC3/AC4/AC5/AC6/AC7/AC8: selectAgents with fake streams ---

test("selectAgents renders the six harness menu to output and Enter installs claude-code", async () => {
  const input = fakeTTY();
  const output = fakeOutput();
  const promise = selectAgents({ input, output });
  press(input, "enter");
  const result = await promise;

  assert.deepEqual(result, ["claude-code"]);
  const text = output.text();
  assert.match(text, /选择要安装的 Agent/);
  assert.match(text, /\[x\] claude-code/);
  for (const agent of HARNESS_AGENTS) assert.match(text, new RegExp(`\\[.?\\] ${agent}\\b`));
  assert.doesNotMatch(text, /\bagents\b/, "the agents alias is not listed");
  assert.match(text, /已选择：claude-code/);
});

test("selectAgents supports arrows, space multi-select and returns canonical order", async () => {
  const input = fakeTTY();
  const output = fakeOutput();
  const promise = selectAgents({ input, output });
  press(input, "up"); // wraps to pi
  press(input, "space"); // select pi
  for (let i = 0; i < 4; i += 1) press(input, "up"); // pi -> deepseek -> hermes -> openclaw -> codex
  press(input, "space"); // select codex
  press(input, "enter");
  assert.deepEqual(await promise, ["claude-code", "codex", "pi"]);
});

test("selectAgents 'a' selects all, a second 'a' clears, Enter then stays open", async () => {
  const input = fakeTTY();
  const output = fakeOutput();
  const promise = selectAgents({ input, output });

  press(input, "a");
  press(input, "a");
  press(input, "enter");
  await flush();
  assert.match(output.text(), /至少选择一个 agent。/);

  press(input, "a");
  press(input, "enter");
  assert.deepEqual(await promise, HARNESS_AGENTS);
});

test("selectAgents does not resolve on an empty confirm and clears the hint afterwards", async () => {
  const input = fakeTTY();
  const output = fakeOutput();
  let settled = false;
  const promise = selectAgents({ input, output }).then((value) => {
    settled = true;
    return value;
  });

  press(input, "space"); // deselect the default claude-code
  press(input, "enter");
  await flush();
  assert.equal(settled, false);
  assert.match(output.text(), /至少选择一个 agent。/);
  assert.doesNotMatch(output.text(), /已选择/, "must not confirm before the hint is resolved");

  press(input, "space"); // select claude-code again
  press(input, "enter");
  assert.deepEqual(await promise, ["claude-code"]);
});

test("selectAgents returns null for q, Ctrl+C and stream end", async () => {
  for (const name of ["q", "ctrlC"]) {
    const input = fakeTTY();
    const output = fakeOutput();
    const promise = selectAgents({ input, output });
    press(input, name);
    assert.equal(await promise, null, name);
  }

  const input = fakeTTY();
  const output = fakeOutput();
  const promise = selectAgents({ input, output });
  input.end();
  assert.equal(await promise, null);
});

test("selectAgents rejects when the input stream errors", async () => {
  const input = fakeTTY();
  const output = fakeOutput();
  const promise = selectAgents({ input, output });
  input.emit("error", new Error("boom"));
  await assert.rejects(promise, /boom/);
  assert.deepEqual(input.rawCalls, [true, false]);
  assert.equal(input.listenerCount("keypress"), 0);
  assert.equal(input.isPaused(), true);
});

test("selectAgents restores raw mode, detaches its listeners and pauses on confirm", async () => {
  const input = fakeTTY();
  const output = fakeOutput();
  const dataBefore = input.listenerCount("data");
  const promise = selectAgents({ input, output });
  press(input, "enter");
  await promise;

  assert.deepEqual(input.rawCalls, [true, false]);
  assert.equal(input.listenerCount("keypress"), 0);
  assert.equal(input.listenerCount("error"), 0);
  assert.equal(input.listenerCount("end"), 0);
  assert.equal(input.listenerCount("close"), 0);
  assert.equal(input.listenerCount("data"), dataBefore, "no data listener may be left behind");
  assert.equal(input.isPaused(), true);
});

test("selectAgents restores the previous raw mode value on cancel", async () => {
  const input = fakeTTY({ raw: true });
  input.rawCalls.push(true);
  const output = fakeOutput();
  const promise = selectAgents({ input, output });
  press(input, "q");
  assert.equal(await promise, null);
  assert.deepEqual(input.rawCalls, [true, true, true]);
  assert.equal(input.isPaused(), true);
});

test("selectAgents cleans up when writing the menu throws", async () => {
  const input = fakeTTY();
  const output = {
    isTTY: true,
    write() {
      throw new Error("kaput");
    },
  };
  await assert.rejects(selectAgents({ input, output }), /kaput/);
  assert.deepEqual(input.rawCalls, [true, false]);
  assert.equal(input.listenerCount("keypress"), 0);
  assert.equal(input.listenerCount("data"), 0);
  assert.equal(input.isPaused(), true);
});

test("selectAgents works without setRawMode and ignores unrelated keys without redrawing", async () => {
  const input = fakeTTY({ withSetRawMode: false });
  const output = fakeOutput();
  const promise = selectAgents({ input, output });
  const writes = output.writes.length;
  press(input, "tab");
  assert.equal(output.writes.length, writes, "ignored keys must not redraw");
  press(input, "enter");
  assert.deepEqual(await promise, ["claude-code"]);
  assert.equal(input.isPaused(), true);
});

test("selectAgents rejects a duplicate agent list before touching streams", async () => {
  const input = fakeTTY();
  const output = fakeOutput();
  await assert.rejects(selectAgents({ agents: ["codex", "codex"], input, output }), /agent/i);
  assert.deepEqual(input.rawCalls, []);
  assert.equal(input.listenerCount("keypress"), 0);
});
