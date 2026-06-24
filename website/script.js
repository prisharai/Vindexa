const gate = document.querySelector("#gate");
const site = document.querySelector("#site");
const openControls = document.querySelectorAll(".crest-button");
const themeToggle = document.querySelector(".theme-toggle");
const runTabs = document.querySelectorAll(".run-tab");
const runStepLabel = document.querySelector("#run-step-label");
const runStepTitle = document.querySelector("#run-step-title");
const runStepCopy = document.querySelector("#run-step-copy");
const runStepCode = document.querySelector("#run-step-code");
const runStepExpected = document.querySelector("#run-step-expected");
const runStepNote = document.querySelector("#run-step-note");
const copyCommand = document.querySelector(".copy-command");
const root = document.documentElement;

const runSteps = [
  {
    label: "Step 1",
    title: "Install the project environment",
    copy:
      "Start from the repository root and install exactly from the lockfile. This keeps the parser, MCP server, benchmark tools, and tests on the same versions used by the repo.",
    code: "cd /Users/prishar/agent-db-safety\nuv sync --frozen",
    expected:
      "Dependencies install from uv.lock without changing the lockfile. If uv reports dependency drift, stop and inspect the diff before continuing.",
    note:
      "Requires Docker and uv. The command should finish without editing dependencies.",
  },
  {
    label: "Step 2",
    title: "Start the seeded Postgres fixture",
    copy:
      "The local database runs Pagila plus generated benchmark tables. It listens on host port 5433 and gives Interdict real tables to classify, simulate, audit, and undo against.",
    code:
      "docker compose up -d\n\ndocker exec -it agent-db-safety-pg \\\n  psql -U postgres -d pagila",
    expected:
      "The container becomes healthy, psql connects to pagila, and seeded tables are available for real policy and simulation checks.",
    note:
      "The first start can take a minute or two because seed data is generated. Later starts reuse the Docker volume.",
  },
  {
    label: "Step 3",
    title: "Run the scripted safety demo",
    copy:
      "This is the fastest proof that the product works locally: it blocks an unsafe write, simulates risky impact, writes audit records, and demonstrates undo for a reversible write.",
    code: "uv run python -m examples.demo",
    expected:
      "The mass update is denied, a scoped risky write is simulated or held for approval, and a reversible write returns an undo_action_id.",
    note:
      "Look for blocked=true, requires_confirmation, simulation rows, and undo_action_id in the output.",
  },
  {
    label: "Step 4",
    title: "Expose Interdict to an agent",
    copy:
      "Start the MCP server. An agent should call run_query through Interdict instead of receiving raw database credentials. Human approval uses an operator token.",
    code:
      "AGENT_OPERATOR_TOKEN=dev-token \\\n  uv run python -m adapters.mcp_server",
    expected:
      "Your MCP client can call run_query and receives structured decisions instead of raw database execution results.",
    note:
      "Register this command in Claude Code, Claude Desktop, Cursor, or another MCP client.",
  },
  {
    label: "Step 5",
    title: "Verify safety and latency gates",
    copy:
      "Run the test suite and benchmark gate before trusting changes. The tests cover parser edge cases, policy, simulation, undo, enforcement, and research utilities.",
    code:
      "uv run pytest\nuv run ruff check .\nuv run black --check .\nuv run python -m benchmarks.ci_latency_gate",
    expected:
      "Tests pass, formatting checks pass, and the latency gate stays under the committed local budget.",
    note:
      "The benchmark is local and hardware-sensitive, but it catches regressions in the pass-through latency budget.",
  },
];

const storedTheme = window.localStorage.getItem("interdict-theme");
if (storedTheme) {
  root.dataset.theme = storedTheme;
}

function openSite() {
  gate.classList.add("is-open");
  site.classList.add("is-visible");
  site.setAttribute("aria-hidden", "false");

  if (!window.location.hash || window.location.hash === "#gate") {
    window.history.replaceState(null, "", "#overview");
  }
}

function updateThemeButton() {
  const isLight = root.dataset.theme === "light";
  themeToggle.setAttribute(
    "aria-label",
    isLight ? "Switch to dark mode" : "Switch to light mode",
  );
}

function toggleTheme() {
  const nextTheme = root.dataset.theme === "light" ? "dark" : "light";
  root.dataset.theme = nextTheme;
  window.localStorage.setItem("interdict-theme", nextTheme);
  updateThemeButton();
}

function updateBlossomDrift() {
  root.style.setProperty("--scroll-y", String(window.scrollY));
}

function showRunStep(index) {
  const step = runSteps[index];
  runTabs.forEach((tab, tabIndex) => {
    tab.classList.toggle("is-active", tabIndex === index);
  });
  runStepLabel.textContent = step.label;
  runStepTitle.textContent = step.title;
  runStepCopy.textContent = step.copy;
  runStepCode.textContent = step.code;
  runStepExpected.textContent = step.expected;
  runStepNote.textContent = step.note;
  copyCommand.textContent = "Copy command";
}

async function copyRunCommand() {
  try {
    await navigator.clipboard.writeText(runStepCode.textContent);
    copyCommand.textContent = "Copied";
  } catch {
    copyCommand.textContent = "Select manually";
  }
}

openControls.forEach((control) => {
  control.addEventListener("click", openSite);
});

themeToggle.addEventListener("click", toggleTheme);
window.addEventListener("scroll", updateBlossomDrift, { passive: true });

runTabs.forEach((tab, index) => {
  tab.addEventListener("click", () => showRunStep(index));
});

copyCommand.addEventListener("click", copyRunCommand);

if (window.location.hash && window.location.hash !== "#gate") {
  openSite();
}

updateThemeButton();
updateBlossomDrift();
showRunStep(0);
