const { existsSync } = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const rootDir = path.resolve(__dirname, "..");
const isWindows = process.platform === "win32";
const npmCommand = isWindows ? "npm.cmd" : "npm";
const pythonPath = path.join(
  rootDir,
  "services",
  "trader",
  ".venv",
  isWindows ? "Scripts" : "bin",
  isWindows ? "python.exe" : "python"
);

if (!existsSync(pythonPath)) {
  console.error(
    [
      "Backend virtual environment was not found.",
      `Expected Python at: ${pythonPath}`,
      "Create it first or adjust the launcher before running `npm run dev`."
    ].join("\n")
  );
  process.exit(1);
}

const children = [];
let shuttingDown = false;
let exitCode = 0;

function startProcess(label, command, args, cwd) {
  const child = spawn(command, args, {
    cwd,
    stdio: "inherit"
  });

  child.on("exit", (code, signal) => {
    if (shuttingDown) {
      return;
    }

    const abnormalExit = signal !== null || (code ?? 0) !== 0;
    if (abnormalExit) {
      exitCode = code ?? 1;
      console.error(`${label} exited unexpectedly${signal ? ` with signal ${signal}` : ` with code ${code}`}.`);
    }
    shutdown(abnormalExit ? code ?? 1 : 0);
  });

  child.on("error", (error) => {
    if (shuttingDown) {
      return;
    }
    exitCode = 1;
    console.error(`${label} failed to start: ${error.message}`);
    shutdown(1);
  });

  children.push(child);
}

function shutdown(code = 0) {
  if (shuttingDown) {
    return;
  }

  shuttingDown = true;
  exitCode = code;

  for (const child of children) {
    if (child.exitCode == null && !child.killed) {
      child.kill("SIGINT");
    }
  }

  setTimeout(() => {
    for (const child of children) {
      if (child.exitCode == null && !child.killed) {
        child.kill("SIGTERM");
      }
    }
    process.exit(exitCode);
  }, 250);
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

console.log("Starting trader backend on port 8011 and frontend dev server...");

startProcess(
  "backend",
  pythonPath,
  ["-m", "uvicorn", "app.main:app", "--reload", "--port", "8011"],
  path.join(rootDir, "services", "trader")
);

startProcess("frontend", npmCommand, ["run", "dev:web"], rootDir);
