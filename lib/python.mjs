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
