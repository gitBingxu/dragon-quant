import readline from "node:readline";
import { HARNESS_AGENTS } from "./paths.mjs";

const HEADER = "选择要安装的 Agent（↑/↓ 移动，空格切换，a 全选/清空，Enter 确认，q 取消）：";
const EMPTY_MESSAGE = "至少选择一个 agent。";
const DEFAULT_SELECTED = ["claude-code"];

// Single TTY gate: the menu is rendered on stderr, so all three streams must be
// terminals. Any redirection (CI, pipe into jq, `2>log`) falls back to the
// non-interactive default instead of waiting invisibly for a keypress.
export function shouldPromptInstall({
  command, agentArg, jsonMode, stdin, stdout, stderr,
} = {}) {
  return command === "install"
    && agentArg === undefined
    && !jsonMode
    && stdin?.isTTY === true
    && stdout?.isTTY === true
    && stderr?.isTTY === true;
}

function assertAgentList(agents) {
  if (!Array.isArray(agents) || agents.length === 0) {
    throw new Error("agents must be a non-empty array");
  }
  const seen = new Set();
  for (const agent of agents) {
    if (typeof agent !== "string" || agent.length === 0) {
      throw new Error("agents must be non-empty strings");
    }
    if (seen.has(agent)) throw new Error(`duplicate agent: ${agent}`);
    seen.add(agent);
  }
}

export function createSelectionState(agents = HARNESS_AGENTS, defaultSelected = DEFAULT_SELECTED) {
  assertAgentList(agents);
  return {
    cursor: 0,
    selected: new Set(defaultSelected.filter((agent) => agents.includes(agent))),
    message: null,
  };
}

export function normalizeSelectionKey(str, key) {
  if (!key || typeof key !== "object") return null;
  if (key.ctrl === true && key.name === "c") return "cancel";
  switch (key.name) {
    case "up":
      return "up";
    case "down":
      return "down";
    case "space":
      return "toggle";
    case "return":
    case "enter":
      return "confirm";
    case "a":
      return key.ctrl || key.meta ? null : "toggle-all";
    case "q":
      return key.ctrl || key.meta ? null : "cancel";
    default:
      return null;
  }
}

// Pure reducer: never mutates `state`. `result` is only present for terminal
// actions (string[] on confirm, null on cancel); non-terminal actions clear the
// hint and redraw.
export function reduceSelection(state, agents, action) {
  switch (action) {
    case "up":
      return {
        state: { ...state, cursor: (state.cursor - 1 + agents.length) % agents.length, message: null },
      };
    case "down":
      return {
        state: { ...state, cursor: (state.cursor + 1) % agents.length, message: null },
      };
    case "toggle": {
      const selected = new Set(state.selected);
      const agent = agents[state.cursor];
      if (selected.has(agent)) selected.delete(agent);
      else selected.add(agent);
      return { state: { ...state, selected, message: null } };
    }
    case "toggle-all": {
      const selected = state.selected.size === agents.length ? new Set() : new Set(agents);
      return { state: { ...state, selected, message: null } };
    }
    case "confirm": {
      if (state.selected.size === 0) {
        return { state: { ...state, message: EMPTY_MESSAGE } };
      }
      return {
        state: { ...state, message: null },
        result: agents.filter((agent) => state.selected.has(agent)),
      };
    }
    case "cancel":
      return { state, result: null };
    default:
      return { state };
  }
}

function renderFrame(state, agents) {
  const lines = [HEADER];
  agents.forEach((agent, index) => {
    const cursor = index === state.cursor ? ">" : " ";
    const box = state.selected.has(agent) ? "[x]" : "[ ]";
    lines.push(`${cursor} ${box} ${agent}`);
  });
  if (state.message) lines.push(state.message);
  return { text: `${lines.join("\n")}\n`, lines: lines.length };
}

// IO shell. Returns the selected agents in `agents` order, or null when the
// user cancels. TTY detection lives only in shouldPromptInstall so the two
// cannot drift apart. All rendering goes to the injected `output`.
export async function selectAgents({
  agents = HARNESS_AGENTS,
  defaultSelected = DEFAULT_SELECTED,
  input = process.stdin,
  output = process.stderr,
} = {}) {
  let state = createSelectionState(agents, defaultSelected);
  const previousRaw = input.isRaw === true;
  const canSetRawMode = typeof input.setRawMode === "function";
  const dataListenersBefore = typeof input.listeners === "function"
    ? new Set(input.listeners("data"))
    : new Set();
  let renderedLines = 0;
  let settled = false;

  const clearFrame = () => {
    if (renderedLines === 0) return;
    try {
      output.write(`\x1b[${renderedLines}A\x1b[0J`);
    } catch {
      // Clearing is best effort and must not mask the real outcome.
    }
    renderedLines = 0;
  };

  const draw = (next) => {
    clearFrame();
    const frame = renderFrame(next, agents);
    output.write(frame.text);
    renderedLines = frame.lines;
  };

  return await new Promise((resolve, reject) => {
    const detach = () => {
      input.removeListener("keypress", onKeypress);
      input.removeListener("error", onError);
      input.removeListener("end", onStreamEnd);
      input.removeListener("close", onStreamEnd);
      // readline attaches a `data` listener when a keypress listener is added.
      // Remove only the listeners this interaction introduced, then pause so the
      // process can exit and nothing keeps draining the terminal.
      if (typeof input.listeners === "function") {
        for (const listener of input.listeners("data")) {
          if (!dataListenersBefore.has(listener)) input.removeListener("data", listener);
        }
      }
    };

    const settle = (error, value) => {
      if (settled) return;
      settled = true;
      detach();
      if (canSetRawMode) {
        try {
          input.setRawMode(previousRaw);
        } catch {
          // The stream may already be destroyed; never change the outcome.
        }
      }
      try {
        input.pause?.();
      } catch {
        // Best effort.
      }
      clearFrame();
      if (error) reject(error);
      else resolve(value);
    };

    function onKeypress(str, key) {
      if (settled) return;
      let action;
      try {
        action = normalizeSelectionKey(str, key);
      } catch (err) {
        settle(err);
        return;
      }
      if (action === null) return;
      let outcome;
      try {
        outcome = reduceSelection(state, agents, action);
      } catch (err) {
        settle(err);
        return;
      }
      state = outcome.state;
      if (outcome.result === null) {
        settle(null, null);
        return;
      }
      if (outcome.result !== undefined) {
        try {
          clearFrame();
          output.write(`已选择：${outcome.result.join(", ")}\n`);
        } catch (err) {
          settle(err);
          return;
        }
        settle(null, outcome.result);
        return;
      }
      try {
        draw(state);
      } catch (err) {
        settle(err);
      }
    }

    function onError(error) {
      settle(error ?? new Error("input stream error"));
    }

    function onStreamEnd() {
      settle(null, null);
    }

    try {
      readline.emitKeypressEvents(input);
      if (canSetRawMode) input.setRawMode(true);
      input.resume?.();
    } catch (err) {
      settle(err);
      return;
    }

    input.on("keypress", onKeypress);
    input.on("error", onError);
    input.on("end", onStreamEnd);
    input.on("close", onStreamEnd);

    try {
      draw(state);
    } catch (err) {
      settle(err);
    }
  });
}
