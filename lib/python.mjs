import { execFileSync } from "node:child_process";

const VERSION_RE = /dragon-quant\s+(\d+\.\d+\.\d+)/;

export function parseCliVersion(text) {
  const match = VERSION_RE.exec(String(text ?? ""));
  return match ? match[1] : null;
}

export function compareVersions(a, b) {
  const left = String(a ?? "0").split(".").map((part) => Number.parseInt(part, 10) || 0);
  const right = String(b ?? "0").split(".").map((part) => Number.parseInt(part, 10) || 0);
  const length = Math.max(left.length, right.length);
  for (let i = 0; i < length; i += 1) {
    const x = left[i] ?? 0;
    const y = right[i] ?? 0;
    if (x < y) return -1;
    if (x > y) return 1;
  }
  return 0;
}

export function meetsMinVersion(version, minVersion) {
  if (!version) return false;
  return compareVersions(version, minVersion) >= 0;
}

function defaultExec(file, args, timeoutMs) {
  return execFileSync(file, args, {
    encoding: "utf8",
    timeout: timeoutMs,
    stdio: ["ignore", "pipe", "pipe"],
  });
}

function combineOutput(result) {
  if (result && typeof result === "object") {
    return `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
  }
  return String(result ?? "");
}

/** Skill 必需的叶子能力（顶层命令名；能力探测只做只读 `-h`）。 */
export const REQUIRED_CLI_CAPABILITIES = [
  "scan",
  "dragons",
  "prep",
  "auction",
  "premarket",
  "review",
  "review-account",
  "factor",
  "dashboard",
];

function defaultProbe(file, args, timeoutMs) {
  return execFileSync(file, args, {
    encoding: "utf8",
    timeout: timeoutMs,
    stdio: ["ignore", "pipe", "pipe"],
  });
}

/**
 * 只读能力探测：最低版本之外，确认本次 CLI 真的暴露 Skill 需要的顶层命令。
 * 返回 `{ok, found, missing, error}`；绝不安装或修改任何东西。
 */
export function probeCliCapabilities({
  command, pythonArg, env = process.env, exec, timeoutMs = 5000,
} = {}) {
  const file = command ?? pythonArg ?? env?.DQ_PYTHON;
  if (!file) {
    return { ok: false, found: [], missing: [...REQUIRED_CLI_CAPABILITIES],
             error: "CLI command unavailable" };
  }
  const run = exec ?? ((bin, args) => defaultProbe(bin, args, timeoutMs));
  const prefixes = [["-m", "dragon_quant"], []];
  const found = [];
  let lastError = null;
  for (const name of REQUIRED_CLI_CAPABILITIES) {
    let ok = false;
    for (const prefix of prefixes) {
      try {
        const output = combineOutput(run(file, [...prefix, name, "-h"]));
        if (!/invalid choice/i.test(output)) {
          ok = true;
          break;
        }
      } catch (err) {
        lastError = err?.message ?? String(err);
      }
    }
    if (ok) found.push(name);
  }
  const missing = REQUIRED_CLI_CAPABILITIES.filter(
    (name) => !found.includes(name));
  return { ok: missing.length === 0, found, missing,
           error: missing.length ? lastError : null };
}

export function detectCli({ pythonArg, env = process.env, exec, timeoutMs = 5000 } = {}) {
  const candidates = [];
  const push = (file, args, source) => {
    if (!file) return;
    const duplicate = candidates.some(
      (item) => item.file === file && item.args.join("\0") === args.join("\0"),
    );
    if (!duplicate) candidates.push({ file, args, source });
  };

  push(pythonArg, ["-m", "dragon_quant", "-v"], "pythonArg");
  push(env?.DQ_PYTHON, ["-m", "dragon_quant", "-v"], "env:DQ_PYTHON");
  push("dragon-quant", ["-v"], "path:dragon-quant");
  push("python3", ["-m", "dragon_quant", "-v"], "path:python3");
  push("python", ["-m", "dragon_quant", "-v"], "path:python");

  const run = exec ?? ((file, args) => defaultExec(file, args, timeoutMs));
  let lastError = null;
  for (const candidate of candidates) {
    let output;
    try {
      output = combineOutput(run(candidate.file, candidate.args));
    } catch (err) {
      lastError = err?.message ?? String(err);
      continue;
    }
    const version = parseCliVersion(output);
    if (!version) {
      lastError = `dragon-quant version not found in output of ${candidate.file}`;
      continue;
    }
    return {
      found: true,
      command: candidate.file,
      version,
      source: candidate.source,
      error: null,
    };
  }

  return {
    found: false,
    command: null,
    version: null,
    source: null,
    error: lastError ?? "dragon-quant CLI not found",
  };
}
