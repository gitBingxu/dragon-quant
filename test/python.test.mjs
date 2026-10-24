import test from "node:test";
import assert from "node:assert/strict";
import { parseCliVersion, compareVersions, detectCli, meetsMinVersion } from "../lib/python.mjs";

test("explicit --python wins", () => {
  const calls = [];
  const exec = (file, args) => { calls.push([file, ...args]); return "dragon-quant 0.7.1\n"; };
  const result = detectCli({ pythonArg: "/venv/bin/python", env: { DQ_PYTHON: "/other/bin/python" }, exec });
  assert.deepEqual(calls[0], ["/venv/bin/python", "-m", "dragon_quant", "-v"]);
  assert.equal(result.found, true);
  assert.equal(result.version, "0.7.1");
  assert.equal(result.source, "pythonArg");
  assert.equal(calls.length, 1);
});

test("DQ_PYTHON is used when no --python", () => {
  const calls = [];
  const exec = (file, args) => { calls.push([file, ...args]); return "dragon-quant 0.7.1\n"; };
  const result = detectCli({ env: { DQ_PYTHON: "/env/bin/python" }, exec });
  assert.deepEqual(calls[0], ["/env/bin/python", "-m", "dragon_quant", "-v"]);
  assert.equal(result.source, "env:DQ_PYTHON");
});

test("falls back to dragon-quant on PATH", () => {
  const calls = [];
  const exec = (file, args) => { calls.push([file, ...args]); return "dragon-quant 0.7.1\n"; };
  const result = detectCli({ env: {}, exec });
  assert.deepEqual(calls[0], ["dragon-quant", "-v"]);
  assert.equal(result.source, "path:dragon-quant");
});

test("falls back to python3 -m dragon_quant", () => {
  const calls = [];
  const exec = (file, args) => {
    calls.push([file, ...args]);
    if (file === "dragon-quant") throw new Error("ENOENT");
    return "dragon-quant 0.7.1\n";
  };
  const result = detectCli({ env: {}, exec });
  assert.deepEqual(calls[0], ["dragon-quant", "-v"]);
  assert.deepEqual(calls[1], ["python3", "-m", "dragon_quant", "-v"]);
  assert.equal(result.source, "path:python3");
});

test("falls back to python -m dragon_quant", () => {
  const calls = [];
  const exec = (file, args) => {
    calls.push([file, ...args]);
    if (file === "dragon-quant" || file === "python3") throw new Error("ENOENT");
    return "dragon-quant 0.7.1\n";
  };
  const result = detectCli({ env: {}, exec });
  assert.deepEqual(calls[2], ["python", "-m", "dragon_quant", "-v"]);
  assert.equal(result.source, "path:python");
});

test("missing CLI returns found false without throwing", () => {
  const exec = () => { throw new Error("ENOENT"); };
  const result = detectCli({ env: {}, exec });
  assert.equal(result.found, false);
  assert.equal(result.command, null);
  assert.equal(result.version, null);
  assert.match(result.error, /ENOENT/);
});

test("old version is detected but meetsMinVersion is false", () => {
  const exec = () => "dragon-quant 0.7.0\n";
  const result = detectCli({ env: {}, exec });
  assert.equal(result.found, true);
  assert.equal(result.version, "0.7.0");
  assert.equal(meetsMinVersion(result.version, "0.7.1"), false);
});

test("parseCliVersion accepts stdout and stderr style output", () => {
  assert.equal(parseCliVersion("dragon-quant 0.7.1\n"), "0.7.1");
  assert.equal(parseCliVersion("noise\n\ndragon-quant 0.7.1\nmore"), "0.7.1");
  assert.equal(parseCliVersion("Traceback...\nValueError: bad"), null);
});

test("compareVersions orders 0.7.0 < 0.7.1 < 0.8.0", () => {
  assert.equal(compareVersions("0.7.0", "0.7.1"), -1);
  assert.equal(compareVersions("0.7.1", "0.7.1"), 0);
  assert.equal(compareVersions("0.8.0", "0.7.1"), 1);
  assert.equal(compareVersions("1.0", "1.0.0"), 0);
});

test("detectCli combines stdout and stderr from the exec result", () => {
  const exec = () => ({ stdout: "", stderr: "dragon-quant 0.7.1\n" });
  const result = detectCli({ env: {}, exec });
  assert.equal(result.version, "0.7.1");
});
