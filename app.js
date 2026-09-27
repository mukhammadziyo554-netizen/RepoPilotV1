const home = document.querySelector("main");
const workspace = document.querySelector("#workspace");
const form = document.querySelector("#analysis-form");
const urlInput = document.querySelector("#repository-url");
const instruction = document.querySelector("#instruction");
const instructionLabel = document.querySelector("#instruction-label");
const modeInputs = [...document.querySelectorAll('input[name="analysis-mode"]')];
const formError = document.querySelector("#form-error");
const analyzeButton = document.querySelector("#analyze-button");
const conversation = document.querySelector("#conversation");
const progress = document.querySelector("#progress-message");
const progressText = progress.querySelector("p");
const status = document.querySelector("#analysis-status");
const title = document.querySelector("#workspace-title");
const repoLink = document.querySelector("#workspace-url");
const repoAvatar = document.querySelector("#repository-avatar");
const returnLatest = document.querySelector("#return-latest");
const chatForm = document.querySelector("#chat-form");
const followUpQuestion = document.querySelector("#follow-up-question");
const followUpSend = document.querySelector("#follow-up-send");
const HISTORY_KEY = "repopilot-conversations-v1";
const languageColors = {
  TypeScript: "#3178c6", JavaScript: "#f1e05a", Python: "#3572a5", CSS: "#663399", HTML: "#e34c26",
  Java: "#b07219", Go: "#00add8", Rust: "#dea584", Ruby: "#701516", PHP: "#4f5d95", Shell: "#89e051",
  C: "#555555", "C++": "#f34b7d", "C#": "#178600", Kotlin: "#a97bff", Swift: "#f05138", Dart: "#00b4ab",
};
let isWorking = false;
let lastCompletedAnalysis = null;
let activeRequest = 0;
let followLatest = true;
let activeConversation = null;
let activeSourceFiles = [];
let retryFollowUp = null;
let savedConversations = loadSavedConversations();
let activeMode = "overview";
let activeArchitectureReport = null;

function prepareOriginalBrandMark() {
  const target = document.querySelector(".brand-mark img");
  const source = new Image();
  source.onload = () => {
    try {
      const sourceCanvas = document.createElement("canvas");
      sourceCanvas.width = source.naturalWidth;
      sourceCanvas.height = source.naturalHeight;
      const context = sourceCanvas.getContext("2d", { willReadFrequently: true });
      context.drawImage(source, 0, 0);
      const imageData = context.getImageData(0, 0, sourceCanvas.width, sourceCanvas.height);
      const { data } = imageData;
      const width = sourceCanvas.width;
      const pixelCount = width * sourceCanvas.height;
      const visited = new Uint8Array(pixelCount);
      const queue = new Int32Array(pixelCount);
      let start = 0;
      let end = 0;
      const isWhiteBackground = index => {
        const offset = index * 4;
        return data[offset] > 245 && data[offset + 1] > 245 && data[offset + 2] > 245 && data[offset + 3] > 0;
      };
      const enqueue = index => {
        if (!visited[index] && isWhiteBackground(index)) {
          visited[index] = 1;
          queue[end++] = index;
        }
      };
      for (let x = 0; x < width; x += 1) {
        enqueue(x);
        enqueue(pixelCount - width + x);
      }
      for (let y = 1; y < sourceCanvas.height - 1; y += 1) {
        enqueue(y * width);
        enqueue(y * width + width - 1);
      }
      while (start < end) {
        const index = queue[start++];
        const offset = index * 4;
        data[offset + 3] = 0;
        const x = index % width;
        if (x > 0) enqueue(index - 1);
        if (x < width - 1) enqueue(index + 1);
        if (index >= width) enqueue(index - width);
        if (index < pixelCount - width) enqueue(index + width);
      }
      context.putImageData(imageData, 0, 0);
      let left = width;
      let top = sourceCanvas.height;
      let right = 0;
      let bottom = 0;
      for (let index = 0; index < pixelCount; index += 1) {
        if (data[index * 4 + 3] === 0) continue;
        const x = index % width;
        const y = Math.floor(index / width);
        left = Math.min(left, x); top = Math.min(top, y); right = Math.max(right, x); bottom = Math.max(bottom, y);
      }
      if (right <= left || bottom <= top) throw new Error("No visible logo pixels");
      const margin = Math.max(4, Math.round(Math.max(right - left, bottom - top) * 0.025));
      const crop = document.createElement("canvas");
      crop.width = right - left + margin * 2 + 1;
      crop.height = bottom - top + margin * 2 + 1;
      crop.getContext("2d").drawImage(sourceCanvas, left - margin, top - margin, crop.width, crop.height, 0, 0, crop.width, crop.height);
      target.src = crop.toDataURL("image/png");
      document.body.classList.add("logo-ready");
    } catch {
      document.body.classList.add("logo-fallback");
    }
  };
  source.onerror = () => document.body.classList.add("logo-fallback");
  source.src = "/ChatGPT%20Image%20Sep%2025%2C%202026%2C%2009_27_09%20PM.png";
}

prepareOriginalBrandMark();

function loadSavedConversations() {
  try {
    const stored = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    return Array.isArray(stored) ? stored.filter(item => item && item.id && item.workspace && Array.isArray(item.messages)) : [];
  } catch {
    return [];
  }
}

function persistConversations() {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(savedConversations.slice(0, 12)));
  } catch {
    // Keep the active browser session usable if local storage is unavailable or full.
  }
}

function compactWorkspace(data) {
  const budget = { remaining: 2_000, shortened: false };
  const compactTree = node => {
    if (!node || budget.remaining-- <= 0) {
      budget.shortened = true;
      return null;
    }
    const copy = { name: node.name, path: node.path, type: node.type };
    if (typeof node.size === "number") copy.size = node.size;
    if (node.children) copy.children = node.children.map(compactTree).filter(Boolean);
    return copy;
  };
  const tree = compactTree(data.tree);
  const coverage = { ...data.discovery_coverage };
  if (budget.shortened) {
    coverage.complete = false;
    coverage.message = "The saved local overview shows a shortened file tree. Run a new analysis to reload the complete GitHub inventory.";
  }
  return {
    repository: data.repository,
    tree,
    file_statistics: data.file_statistics,
    language_statistics: data.language_statistics,
    characteristics: data.characteristics,
    discovery_coverage: coverage,
    retrieval_coverage: data.retrieval_coverage,
    analysis: data.analysis,
  };
}

function updateConversation(record) {
  record.updatedAt = new Date().toISOString();
  const index = savedConversations.findIndex(item => item.id === record.id);
  if (index >= 0) savedConversations.splice(index, 1);
  savedConversations.unshift(record);
  savedConversations = savedConversations.slice(0, 12);
  persistConversations();
}

function mergeSourceFiles(nextFiles) {
  const merged = new Map(activeSourceFiles.map(file => [file.path, file]));
  (nextFiles || []).forEach(file => merged.set(file.path, file));
  activeSourceFiles = [...merged.values()];
}

function setWorking(value) {
  isWorking = value;
  analyzeButton.disabled = value;
  if (activeConversation) {
    followUpQuestion.disabled = value;
    followUpSend.disabled = value;
  }
}

function selectedMode() {
  return modeInputs.find(input => input.checked)?.value || "overview";
}

function updateModeForm() {
  activeMode = selectedMode();
  const isCustom = activeMode === "custom";
  instructionLabel.innerHTML = isCustom
    ? 'Your question <span>Required</span>'
    : 'Optional analysis focus <span>Optional</span>';
  instruction.placeholder = isCustom
    ? "Ask a specific question about this repository."
    : activeMode === "architecture"
      ? "Optionally focus the architecture on an area such as API routes or authentication."
      : "Optionally focus the short overview on an area such as authentication or API routes.";
  instruction.required = isCustom;
}

modeInputs.forEach(input => input.addEventListener("change", updateModeForm));
updateModeForm();

function setProgress(message, visible = true) {
  progressText.textContent = message;
  progress.hidden = !visible;
  status.textContent = visible ? "Analyzing" : "Ready";
}

function showWorkspace() {
  home.hidden = true;
  workspace.hidden = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showHome() {
  workspace.hidden = true;
  home.hidden = false;
  formError.textContent = "";
  urlInput.value = "";
  instruction.value = "";
  chatForm.hidden = true;
  activeArchitectureReport = null;
  window.scrollTo({ top: 0, behavior: "smooth" });
  urlInput.focus();
}

function addText(parent, tag, text, className = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text || "Not provided";
  parent.append(element);
  return element;
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
}

function renderTable(tableText) {
  const rows = tableText.trim().split("\n").map(row => row.trim().replace(/^\||\|$/g, "").split("|").map(cell => cell.trim()));
  const headers = rows.shift() || [];
  rows.shift(); // Markdown's alignment separator is presentation-only.
  const header = headers.map(cell => `<th scope="col">${cell}</th>`).join("");
  const body = rows.map(row => `<tr>${headers.map((_, index) => `<td>${row[index] || ""}</td>`).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function formatMarkdown(value, sourceFiles = []) {
  const verifiedPaths = new Set(sourceFiles.map(file => file.path));
  let safe = escapeHtml(value);
  safe = safe.replace(/```([^\n]*)\n([\s\S]*?)```/g, (_match, language, code) => `<pre class="code-block"><button type="button" class="copy-code">Copy</button><code data-language="${language}">${code}</code></pre>`);
  safe = safe.replace(/(^|\n)(\|[^\n]+\|\n\|[\s:|-]+\|(?:\n\|[^\n]+\|)+)/g, (_match, prefix, table) => `${prefix}${renderTable(table)}`);
  safe = safe.replace(/^### (.+)$/gm, "<h4>$1</h4>").replace(/^## (.+)$/gm, "<h3>$1</h3>").replace(/^# (.+)$/gm, "<h2>$1</h2>");
  safe = safe.replace(/^[-*] (.+)$/gm, "<li>$1</li>");
  safe = safe.replace(/(^|\n)(<li>[\s\S]*?<\/li>(?:\n<li>[\s\S]*?<\/li>)*)/g, "$1<ul>$2</ul>");
  safe = safe.replace(/^&gt; (.+)$/gm, "<aside class=\"analysis-callout\">$1</aside>");
  safe = safe.replace(/`([^`]+)`/g, (_match, code) => verifiedPaths.has(code)
    ? `<button type="button" class="source-reference" data-path="${code}">${code}</button>`
    : `<code class="inline-code">${code}</code>`)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  return safe.replace(/\n(?!<\/?(?:li|ul|h[2-4]|pre|code))/g, "<br>");
}

function splitAnalysisSections(value) {
  const legacySectionTitles = "Project Summary|Inspected Architecture|Important Files|Engineering Observations|Coverage and Limitations|Architecture Notes|Recommendations for Inspection";
  const text = String(value || "").trim().replace(new RegExp(`^(${legacySectionTitles})\\s*$`, "gmi"), "## $1");
  const headingPattern = /^##\s+(.+?)\s*$/gm;
  const headings = [...text.matchAll(headingPattern)];
  if (!headings.length) return [{ title: "Response", body: text, wide: true }];

  const sections = [];
  const introduction = text.slice(0, headings[0].index).trim();
  headings.forEach((heading, index) => {
    const bodyStart = heading.index + heading[0].length;
    const bodyEnd = index + 1 < headings.length ? headings[index + 1].index : text.length;
    const body = `${index === 0 && introduction ? `${introduction}\n\n` : ""}${text.slice(bodyStart, bodyEnd).trim()}`.trim();
    if (!body) return;
    const title = heading[1].replace(/[*_`]/g, "").trim();
    const wide = /\|.+\||```|^###\s/m.test(body) || body.length > 360 || /summary|architecture|files|observations|coverage/i.test(title);
    sections.push({ title, body, wide });
  });
  return sections.length ? sections : [{ title: "Response", body: text, wide: true }];
}

function renderAnalysisResponse(container, value, sourceFiles = []) {
  const sections = splitAnalysisSections(value);
  container.replaceChildren();
  container.classList.toggle("analysis-markdown--structured", sections.length > 1);
  sections.forEach(section => {
    const card = document.createElement("section");
    card.className = `analysis-response-card${section.wide ? " analysis-response-card--wide" : ""}`;
    if (section.title !== "Response") addText(card, "h3", section.title, "analysis-response-title");
    const body = document.createElement("div");
    body.className = "analysis-response-body";
    body.innerHTML = formatMarkdown(section.body, sourceFiles);
    card.append(body);
    container.append(card);
  });
}

function renderStreamingText(container, value) {
  container.classList.remove("generating", "analysis-markdown--structured");
  let output = container.querySelector(".analysis-stream-output");
  if (!output) {
    container.replaceChildren();
    output = document.createElement("div");
    output.className = "analysis-stream-output";
    container.append(output);
  }
  // Keep one DOM node alive while native provider chunks arrive. Recreating
  // response cards on every token replayed their enter animation and looked like blinking.
  output.textContent = value;
}

function setGeneratingState(content, label) {
  content.classList.add("generating");
  content.replaceChildren();
  const thinking = document.createElement("span");
  thinking.className = "analysis-thinking";
  thinking.setAttribute("aria-hidden", "true");
  thinking.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
  const text = document.createElement("span");
  text.textContent = label;
  content.append(thinking, text);
}

function isNearBottom() {
  return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 180;
}

function followIfAppropriate() {
  if (followLatest) {
    returnLatest.hidden = true;
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
  } else {
    returnLatest.hidden = false;
  }
}

async function streamRequest(endpoint, payload, onEvent) {
  const response = await fetch(endpoint, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error?.message || "RepoPilot could not start analysis.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop();
    for (const block of blocks) {
      const event = block.match(/^event: (.+)$/m)?.[1] || "message";
      const raw = block.match(/^data: (.+)$/m)?.[1];
      if (raw) onEvent(event, JSON.parse(raw));
    }
    if (done) break;
  }
}

function languageColor(name) {
  if (languageColors[name]) return languageColors[name];
  let hash = 0;
  for (const character of name) hash = ((hash << 5) - hash) + character.charCodeAt(0);
  return `hsl(${Math.abs(hash) % 360} 54% 48%)`;
}

function formatFileSize(bytes) {
  if (!Number.isFinite(bytes)) return "Size not reported";
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(bytes < 10_240 ? 1 : 0)} KB`;
}

function fileKindLabel(path) {
  const name = String(path || "").split("/").pop().toLowerCase();
  if (name === "readme" || name === "readme.md") return "Documentation";
  if (["package.json", "pyproject.toml", "go.mod", "cargo.toml", "requirements.txt"].includes(name)) return "Manifest";
  const extension = name.includes(".") ? name.split(".").pop().toUpperCase() : "File";
  return `${extension} file`;
}

function makeTreeNode(node, depth = 0) {
  if (depth === 0) {
    const root = document.createElement("div");
    root.className = "tree-root";
    (node.children || []).forEach(child => root.append(makeTreeNode(child, depth + 1)));
    return root;
  }
  const isDirectory = node.type === "directory";
  if (isDirectory) {
    const details = document.createElement("details");
    details.className = "tree-directory";
    details.open = depth === 0;
    const summary = document.createElement("summary");
    addText(summary, "span", "▸", "tree-caret");
    addText(summary, "span", "▣", "tree-icon folder-icon");
    addText(summary, "span", node.name, "tree-name");
    summary.title = node.path || node.name;
    details.append(summary);
    const children = document.createElement("div");
    children.className = "tree-children";
    details.append(children);
    let populated = false;
    const populate = () => {
      if (populated) return;
      (node.children || []).forEach(child => children.append(makeTreeNode(child, depth + 1)));
      populated = true;
    };
    if (depth === 0) populate();
    details.addEventListener("toggle", () => { if (details.open) populate(); });
    return details;
  }
  const file = document.createElement("div");
  file.className = "tree-file";
  addText(file, "span", "‹/›", "tree-icon file-icon");
  addText(file, "span", node.name, "tree-name");
  if (typeof node.size === "number") addText(file, "span", formatFileSize(node.size), "tree-size");
  file.title = node.path || node.name;
  return file;
}

function renderLanguages(statistics) {
  const section = document.createElement("section");
  section.className = "language-section";
  addText(section, "h3", "Languages");
  const languages = statistics?.languages || [];
  if (!languages.length) {
    addText(section, "p", "GitHub did not report repository-wide language statistics.", "empty-note");
    return section;
  }
  const bar = document.createElement("div");
  bar.className = "language-bar";
  languages.forEach(language => {
    const segment = document.createElement("span");
    segment.style.width = `${language.percentage}%`;
    segment.style.background = languageColor(language.name);
    segment.title = `${language.name}: ${language.percentage}% (${language.bytes.toLocaleString()} bytes)`;
    bar.append(segment);
  });
  section.append(bar);
  const legend = document.createElement("div");
  legend.className = "language-legend";
  languages.forEach(language => {
    const item = document.createElement("span");
    const dot = document.createElement("i");
    dot.style.background = languageColor(language.name);
    item.append(dot, document.createTextNode(`${language.name} ${language.percentage}%`));
    legend.append(item);
  });
  section.append(legend);
  addText(section, "p", "Repository-wide byte distribution reported by GitHub; it is separate from the selected files used for analysis.", "data-source-note");
  return section;
}

function renderOverview(data) {
  const repository = data.repository;
  title.textContent = repository.full_name;
  repoLink.textContent = "View on GitHub ↗";
  repoLink.href = repository.url;
  repoAvatar.hidden = false;
  repoAvatar.src = `https://github.com/${encodeURIComponent(repository.owner.login)}.png?size=96`;
  repoAvatar.alt = `${repository.owner.login} profile image`;
  repoAvatar.onerror = () => { repoAvatar.hidden = true; };
  conversation.replaceChildren();
  activeArchitectureReport = null;

  const layout = document.createElement("section");
  layout.className = "repository-workspace";
  const primary = document.createElement("div");
  primary.className = "repository-primary";
  const overview = document.createElement("article");
  overview.className = "repository-overview repository-summary";
  const heading = document.createElement("div");
  heading.className = "overview-heading";
  const ownerName = document.createElement("div");
  addText(ownerName, "p", `${repository.owner.login} / ${repository.name}`, "repository-owner");
  heading.append(ownerName);
  addText(heading, "span", repository.visibility || "public", "visibility-badge");
  overview.append(heading);

  const metadata = document.createElement("div");
  metadata.className = "metadata-line";
  [
    repository.primary_language || "Language not detected",
    `Branch: ${repository.default_branch}`,
    repository.archived ? "Archived" : "Active repository",
    `Commit: ${repository.latest_commit.sha.slice(0, 12)}`,
  ].forEach(value => addText(metadata, "span", value));
  overview.append(metadata);

  const statistics = document.createElement("div");
  statistics.className = "overview-stats";
  [
    ["Files", data.file_statistics.files], ["Directories", data.file_statistics.directories],
    ["Stars", repository.stars], ["Forks", repository.forks],
  ].forEach(([label, value]) => {
    const statistic = document.createElement("div");
    addText(statistic, "span", label);
    addText(statistic, "strong", String(value));
    statistics.append(statistic);
  });
  overview.append(statistics);
  primary.append(overview);

  const explorer = document.createElement("section");
  explorer.className = "file-explorer";
  const explorerHeading = document.createElement("div");
  explorerHeading.className = "explorer-heading";
  addText(explorerHeading, "h2", "Files");
  addText(explorerHeading, "span", `${data.file_statistics.files.toLocaleString()} files`, "file-count");
  explorer.append(explorerHeading, makeTreeNode(data.tree));
  const treeCoverage = addText(explorer, "p", data.discovery_coverage.message, "coverage-note");
  if (!data.discovery_coverage.complete) treeCoverage.classList.add("coverage-partial");
  primary.append(explorer);

  const aside = document.createElement("aside");
  aside.className = "repository-aside";
  addText(aside, "p", "VERIFIED GITHUB DATA", "discovery-label");
  addText(aside, "h2", "About");
  addText(aside, "p", repository.description || "No repository description was provided by GitHub.", "repository-description");
  const facts = document.createElement("dl");
  facts.className = "repository-facts";
  [
    ["Branch", repository.default_branch],
    ["Commit", repository.latest_commit.sha.slice(0, 12)],
    ["License", repository.license || "Not reported"],
    ["Status", repository.archived ? "Archived" : "Active"],
  ].forEach(([label, value]) => {
    addText(facts, "dt", label);
    addText(facts, "dd", value);
  });
  aside.append(facts, renderLanguages(data.language_statistics));
  layout.append(primary, aside);
  conversation.append(layout);
}

const architectureKinds = {
  frontend: "#2563eb", backend: "#7c3aed", api: "#0891b2", database: "#b45309",
  library: "#0f766e", cli: "#4f46e5", service: "#be185d", config: "#64748b",
};
const architectureRelationships = new Set(["imports", "calls", "exposes", "uses", "owns", "depends on"]);

function architectureModuleId(path) {
  const parent = String(path || "").includes("/") ? String(path).split("/").slice(0, -1).join("/") : "root";
  return (parent.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "root");
}

function validateArchitecturePayload(parsed, sourceFiles = []) {
  if (!parsed || typeof parsed !== "object") return null;
  const sourcePaths = new Set(sourceFiles.map(file => file.path));
  try {
    const moduleIds = new Set();
    const modules = (Array.isArray(parsed.modules) ? parsed.modules : []).slice(0, 8).filter(item => {
      if (!item || typeof item.id !== "string" || !/^[a-z0-9-]{1,40}$/i.test(item.id) || moduleIds.has(item.id) || !String(item.label || "").trim()) return false;
      const paths = Array.isArray(item.paths) ? item.paths.filter(path => sourcePaths.has(path)) : [];
      if (!paths.length) return false;
      moduleIds.add(item.id); return true;
    }).map(item => ({ id: item.id, label: String(item.label).trim().slice(0, 52), paths: item.paths.filter(path => sourcePaths.has(path)).slice(0, 12) }));
    const components = Array.isArray(parsed.components) ? parsed.components.slice(0, 14) : [];
    const ids = new Set();
    const validComponents = components.filter(item => {
      if (!item || typeof item.id !== "string" || !/^[a-z0-9-]{1,40}$/i.test(item.id) || ids.has(item.id)) return false;
      ids.add(item.id);
      return typeof item.label === "string" && item.label.trim() && Array.isArray(item.paths) && item.paths.some(path => sourcePaths.has(path));
    }).map(item => ({
      id: item.id, label: item.label.trim().slice(0, 46), kind: architectureKinds[item.kind] ? item.kind : "library",
      paths: item.paths.filter(path => typeof path === "string" && sourcePaths.has(path)).slice(0, 3),
      description: typeof item.description === "string" ? item.description.trim().slice(0, 180) : "",
      module: moduleIds.has(item.module) ? item.module : architectureModuleId(item.paths.find(path => sourcePaths.has(path))),
    }));
    if (!validComponents.length) return null;
    validComponents.forEach(component => {
      if (modules.some(module => module.id === component.module)) return;
      modules.push({ id: component.module, label: component.module === "root" ? "Repository root" : component.module.replace(/-/g, "/"), paths: [] });
    });
    validComponents.forEach(component => {
      const module = modules.find(item => item.id === component.module);
      component.paths.forEach(path => { if (!module.paths.includes(path)) module.paths.push(path); });
    });
    const validIds = new Set(validComponents.map(item => item.id));
    const componentsById = new Map(validComponents.map(item => [item.id, item]));
    const connections = (Array.isArray(parsed.connections) ? parsed.connections : []).filter(item => {
      const evidence = Array.isArray(item?.evidence_paths) ? item.evidence_paths.filter(path => sourcePaths.has(path)) : [];
      return item && validIds.has(item.from) && validIds.has(item.to) && item.from !== item.to
        && architectureRelationships.has(String(item.label || "").toLowerCase())
        && evidence.some(path => componentsById.get(item.from).paths.includes(path));
    }).slice(0, 18).map(item => ({
      from: item.from, to: item.to, label: String(item.label).toLowerCase().slice(0, 64),
      evidence_paths: item.evidence_paths.filter(path => sourcePaths.has(path)).slice(0, 3),
    }));
    return { modules, components: validComponents, connections, limitations: String(parsed.limitations || "").slice(0, 280) };
  } catch {
    return null;
  }
}

function architecturePayload(analysis, sourceFiles = []) {
  // The API supplies normalized data. The text parser is only for browser-local
  // results created before that contract existed.
  if (analysis?.architecture) return validateArchitecturePayload(analysis.architecture, sourceFiles);
  const text = typeof analysis === "string" ? analysis : analysis?.text;
  const block = String(text || "").match(/```repopilot-architecture\s*\n([\s\S]*?)```/i);
  if (!block) return null;
  try {
    return validateArchitecturePayload(JSON.parse(block[1]), sourceFiles);
  } catch {
    return null;
  }
}

function stripArchitecturePayload(text) {
  return String(text || "").replace(/```repopilot-architecture\s*\n[\s\S]*?```\s*/i, "").trim();
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
  return element;
}

function addArchitectureSourceLinks(parent, paths, sourceFiles) {
  paths.filter(path => sourceFiles.some(file => file.path === path)).forEach(path => {
    const reference = document.createElement("button");
    reference.type = "button";
    reference.className = "source-reference";
    reference.dataset.path = path;
    reference.textContent = path;
    parent.append(reference);
  });
}

function renderArchitectureWorkspace(target, data) {
  const report = architecturePayload(data.analysis, data.source_files || []);
  if (!report) return null;
  const section = document.createElement("section");
  section.className = "architecture-workspace";
  addText(section, "p", "VISUAL ARCHITECTURE", "discovery-label");
  const heading = document.createElement("div");
  heading.className = "architecture-heading";
  addText(heading, "h2", "Repository architecture");
  const download = document.createElement("button");
  download.type = "button";
  download.className = "download-architecture-pdf";
  download.textContent = "Download PDF";
  heading.append(download);
  section.append(heading);

  const width = 1000;
  const nodeWidth = 272;
  const nodeHeight = 88;
  const moduleGroups = report.modules.map(module => ({
    ...module,
    components: report.components.filter(component => component.module === module.id),
  })).filter(module => module.components.length);
  const positions = new Map();
  let nextY = 30;
  moduleGroups.forEach(module => {
    const columns = Math.min(3, Math.max(1, module.components.length));
    const rows = Math.ceil(module.components.length / columns);
    module.x = 28;
    module.y = nextY;
    module.width = width - 56;
    module.height = 60 + rows * 116 + 22;
    module.components.forEach((component, index) => {
      const column = index % columns;
      const row = Math.floor(index / columns);
      const available = module.width - 52;
      const x = module.x + 26 + column * (available - nodeWidth) / Math.max(1, columns - 1);
      positions.set(component.id, { x, y: module.y + 52 + row * 116 });
    });
    nextY += module.height + 24;
  });
  const height = Math.max(280, nextY + 4);
  const diagram = svgElement("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Verified repository architecture diagram" });
  const defs = svgElement("defs");
  const markerId = `architecture-arrow-${Date.now()}`;
  const marker = svgElement("marker", { id: markerId, viewBox: "0 0 10 10", refX: "8", refY: "5", markerWidth: "6", markerHeight: "6", orient: "auto-start-reverse" });
  marker.append(svgElement("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#91a4bb" })); defs.append(marker); diagram.append(defs);
  moduleGroups.forEach(module => {
    const group = svgElement("g", { class: "architecture-module" });
    group.append(svgElement("rect", { x: module.x, y: module.y, width: module.width, height: module.height, rx: "14", fill: "#f7faff", stroke: "#cbdcec", "stroke-width": "1.5" }));
    const title = svgElement("text", { x: module.x + 19, y: module.y + 28, fill: "#314a68", "font-size": "13", "font-weight": "700", "letter-spacing": ".35" });
    title.textContent = module.label; group.append(title);
    const count = svgElement("text", { x: module.x + module.width - 18, y: module.y + 28, fill: "#71849b", "font-size": "10", "text-anchor": "end" });
    count.textContent = `${module.components.length} ${module.components.length === 1 ? "file" : "files"}`; group.append(count);
    diagram.append(group);
  });
  report.connections.forEach(connection => {
    const from = positions.get(connection.from); const to = positions.get(connection.to);
    if (!from || !to) return;
    const startX = from.x + nodeWidth / 2; const startY = from.y + nodeHeight;
    const endX = to.x + nodeWidth / 2; const endY = to.y;
    const middleY = Math.round((startY + endY) / 2);
    const line = svgElement("path", { d: `M ${startX} ${startY} V ${middleY} H ${endX} V ${endY}`, fill: "none", stroke: "#70849a", "stroke-width": "1.8", "marker-end": `url(#${markerId})` });
    diagram.append(line);
    const label = svgElement("text", { x: (startX + endX) / 2, y: middleY - 6, fill: "#526b85", "font-size": "10", "font-weight": "700", "text-anchor": "middle", class: "architecture-edge-label" });
    label.textContent = connection.label; diagram.append(label);
  });
  report.components.forEach(component => {
    const position = positions.get(component.id);
    if (!position) return;
    const group = svgElement("g", { class: "architecture-node" });
    group.append(svgElement("rect", { x: position.x, y: position.y, width: nodeWidth, height: nodeHeight, rx: "9", fill: "#ffffff", stroke: architectureKinds[component.kind], "stroke-width": "2" }));
    const kind = svgElement("text", { x: position.x + 14, y: position.y + 22, fill: architectureKinds[component.kind], "font-size": "9", "font-weight": "700", "letter-spacing": "1" }); kind.textContent = component.kind.toUpperCase(); group.append(kind);
    const label = svgElement("text", { x: position.x + 14, y: position.y + 47, fill: "#172b45", "font-size": "14", "font-weight": "700" }); label.textContent = component.label; group.append(label);
    const path = svgElement("text", { x: position.x + 14, y: position.y + 69, fill: "#64748b", "font-size": "10" }); path.textContent = component.paths[0]; group.append(path);
    diagram.append(group);
  });
  const diagramWrap = document.createElement("div"); diagramWrap.className = "architecture-diagram"; diagramWrap.append(diagram); section.append(diagramWrap);
  const componentGrid = document.createElement("div"); componentGrid.className = "architecture-components";
  report.components.forEach(component => {
    const card = document.createElement("article"); card.className = "architecture-component";
    const module = report.modules.find(item => item.id === component.module);
    addText(card, "span", `${module?.label || "Verified files"} · ${component.kind}`, "architecture-kind"); addText(card, "h3", component.label);
    if (component.description) addText(card, "p", component.description);
    const paths = document.createElement("div"); paths.className = "architecture-paths"; addArchitectureSourceLinks(paths, component.paths, data.source_files || []); card.append(paths); componentGrid.append(card);
  });
  section.append(componentGrid);
  if (report.connections.length) {
    const relationships = document.createElement("div"); relationships.className = "architecture-relationships";
    addText(relationships, "h3", "Verified relationships");
    const list = document.createElement("ul");
    report.connections.forEach(connection => {
      const from = report.components.find(component => component.id === connection.from)?.label || connection.from;
      const to = report.components.find(component => component.id === connection.to)?.label || connection.to;
      const item = document.createElement("li");
      item.append(document.createTextNode(`${from} → ${to}: ${connection.label} `));
      addArchitectureSourceLinks(item, connection.evidence_paths || [], data.source_files || []);
      list.append(item);
    }); relationships.append(list); section.append(relationships);
  }
  if (report.limitations) addText(section, "p", report.limitations, "coverage-note");
  conversation.insertBefore(section, target.section);
  activeArchitectureReport = { report, data, section };
  return report;
}

function startAnalysisConversation(repository, focus) {
  const section = document.createElement("section");
  section.className = "analysis-section";
  addText(section, "p", "REPOSITORY ANALYSIS", "discovery-label");
  addText(section, "h2", "Source-grounded analysis");
  const user = document.createElement("article");
  user.className = "analysis-message user-request";
  addText(user, "p", "YOUR REQUEST", "message-label");
  addText(user, "p", focus || "Provide the short repository overview.");
  section.append(user);
  const assistant = document.createElement("article");
  assistant.className = "analysis-message assistant-response";
  addText(assistant, "p", "REPOPILOT", "message-label");
  const content = document.createElement("div");
  content.className = "analysis-markdown generating";
  setGeneratingState(content, "Preparing a source-grounded analysis…");
  assistant.append(content);
  section.append(assistant);
  conversation.append(section);
  return { section, content };
}

function createFollowUpResponse() {
  const section = conversation.querySelector(".analysis-section") || document.createElement("section");
  if (!section.parentElement) {
    section.className = "analysis-section";
    addText(section, "p", "REPOSITORY ANALYSIS", "discovery-label");
    addText(section, "h2", "Source-grounded conversation");
    conversation.append(section);
  }
  const assistant = document.createElement("article");
  assistant.className = "analysis-message assistant-response";
  addText(assistant, "p", "REPOPILOT", "message-label");
  const content = document.createElement("div");
  content.className = "analysis-markdown generating";
  setGeneratingState(content, "Reviewing the relevant source files…");
  assistant.append(content);
  section.append(assistant);
  return { section, content, assistant };
}

function appendConversationUser(section, text) {
  const user = document.createElement("article");
  user.className = "analysis-message user-request";
  addText(user, "p", "YOUR REQUEST", "message-label");
  addText(user, "p", text);
  section.append(user);
  return user;
}

function appendConversationAssistant(section, message, sourceFiles = []) {
  const assistant = document.createElement("article");
  assistant.className = "analysis-message assistant-response";
  addText(assistant, "p", "REPOPILOT", "message-label");
  const content = document.createElement("div");
  content.className = "analysis-markdown";
  renderAnalysisResponse(content, message.text, sourceFiles);
  assistant.append(content);
  if (message.commit_sha) addText(assistant, "p", `Verified source context · commit ${message.commit_sha.slice(0, 12)}`, "analysis-meta");
  section.append(assistant);
  return assistant;
}

function completeAnalysisConversation(target, data) {
  const analysis = data.analysis;
  target.content.classList.remove("generating");
  const isArchitecture = data.retrieval_coverage?.mode === "architecture";
  const architecture = isArchitecture ? renderArchitectureWorkspace(target, data) : null;
  renderAnalysisResponse(target.content, isArchitecture ? stripArchitecturePayload(analysis.text) : analysis.text, data.source_files);
  if (isArchitecture && !architecture) addText(target.section, "p", "A visual diagram was not shown because the response did not contain enough verified file-and-relationship data. The source-grounded explanation is available below.", "coverage-note");
  const meta = document.createElement("p");
  meta.className = "analysis-meta";
  meta.textContent = `${analysis.source_file_count} inspected files · commit ${analysis.commit_sha.slice(0, 12)}`;
  target.section.append(meta);
  const coverage = addText(target.section, "p", data.retrieval_coverage.message, "coverage-note");
  if (!data.retrieval_coverage.complete) coverage.classList.add("coverage-partial");
}

function exportArchitecturePdf() {
  if (!activeArchitectureReport) return;
  const { report, data, section } = activeArchitectureReport;
  const repository = data.repository;
  const diagram = section.querySelector("svg")?.outerHTML || "";
  const componentRows = report.components.map(component => `<tr><td>${escapeHtml(component.label)}</td><td>${escapeHtml(component.kind)}</td><td>${escapeHtml(component.description || "Not described")}</td><td>${escapeHtml(component.paths.join(", ") || "No path provided")}</td></tr>`).join("");
  const coverage = data.retrieval_coverage;
  const popup = window.open("", "_blank");
  if (!popup) return;
  popup.opener = null;
  popup.document.write(`<!doctype html><html><head><title>${escapeHtml(repository.full_name)} architecture report</title><style>@page{size:A4;margin:16mm}body{font:13px Inter,Arial,sans-serif;color:#182b44}h1{font-size:25px;margin:0 0 5px}h2{margin-top:28px;font-size:17px}p{line-height:1.55}.meta{color:#607089;font-size:11px}.diagram{break-inside:avoid;border:1px solid #dbe5f0;border-radius:10px;padding:12px}.diagram svg{width:100%;height:auto}table{width:100%;border-collapse:collapse;font-size:10px}th,td{padding:8px;border:1px solid #dbe5f0;text-align:left;vertical-align:top}th{background:#f3f7fb}.note{margin-top:18px;padding:10px 12px;background:#f3f7fb;border-left:3px solid #0968f3}@media print{button{display:none}}</style></head><body><h1>${escapeHtml(repository.full_name)}</h1><p>${escapeHtml(repository.description || "No GitHub description was provided.")}</p><p class="meta">Owner: ${escapeHtml(repository.owner.login)} · Commit: ${escapeHtml(coverage.commit_sha)} · Generated: ${new Date().toLocaleString()}</p><h2>Architecture diagram</h2><div class="diagram">${diagram}</div><h2>Verified components</h2><table><thead><tr><th>Component</th><th>Kind</th><th>Role</th><th>Source files</th></tr></thead><tbody>${componentRows}</tbody></table><p class="note">Coverage: ${escapeHtml(coverage.message)}${report.limitations ? ` ${escapeHtml(report.limitations)}` : ""}</p></body></html>`);
  popup.document.close();
  popup.focus();
  setTimeout(() => popup.print(), 250);
}

function showSourceFile(path, sourceFiles) {
  const source = sourceFiles.find(file => file.path === path);
  if (!source) return;
  let inspector = conversation.querySelector(".source-inspector");
  if (!inspector) {
    inspector = document.createElement("section");
    inspector.className = "source-inspector";
    conversation.append(inspector);
  }
  inspector.replaceChildren();
  addText(inspector, "p", "VERIFIED RETRIEVED SOURCE", "discovery-label");
  addText(inspector, "h2", source.path);
  addText(inspector, "p", `${source.language || "Text"} · ${source.size.toLocaleString()} bytes · commit ${source.commit_sha.slice(0, 12)}`, "analysis-meta");
  const block = document.createElement("pre");
  block.className = "code-block";
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "copy-code";
  copy.textContent = "Copy";
  const code = document.createElement("code");
  code.textContent = source.content;
  block.append(copy, code);
  inspector.append(block);
  inspector.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderCompletedAnalysis(data) {
  renderOverview(data);
  if (activeConversation?.messages?.length) {
    const section = document.createElement("section");
    section.className = "analysis-section";
    addText(section, "p", "REPOSITORY ANALYSIS", "discovery-label");
    addText(section, "h2", "Source-grounded conversation");
    conversation.append(section);
    if (activeConversation.mode === "architecture") {
      const initialArchitecture = activeConversation.messages.find(message => message.role === "assistant");
      if (initialArchitecture) renderArchitectureWorkspace({ section }, {
        ...data,
        analysis: initialArchitecture,
        retrieval_coverage: data.retrieval_coverage || { mode: "architecture", message: "Restored browser-local architecture context.", commit_sha: initialArchitecture.commit_sha || "" },
        source_files: activeSourceFiles,
      });
    }
    activeConversation.messages.forEach(message => {
      if (message.role === "user") appendConversationUser(section, message.text);
      if (message.role === "assistant") appendConversationAssistant(section, message, activeSourceFiles);
    });
  } else if (data.analysis) {
    const target = startAnalysisConversation(data.repository, data.retrieval_coverage?.instruction || "");
    completeAnalysisConversation(target, data);
  }
  chatForm.hidden = !activeConversation;
  setProgress(activeConversation?.messages?.length ? "Analysis restored from this browser session." : "Repository overview restored.", false);
}

function restoreConversation(record) {
  activeConversation = record;
  activeSourceFiles = [];
  retryFollowUp = null;
  renderCompletedAnalysis(record.workspace);
  title.textContent = record.workspace.repository.full_name;
  repoLink.textContent = "View on GitHub ↗";
  repoLink.href = record.workspace.repository.url;
  chatForm.hidden = false;
}

function renderRepositoryHistory() {
  home.hidden = true;
  workspace.hidden = false;
  chatForm.hidden = true;
  title.textContent = "My repositories";
  repoAvatar.hidden = true;
  repoLink.textContent = "";
  repoLink.removeAttribute("href");
  status.textContent = "Saved locally";
  conversation.replaceChildren();
  const section = document.createElement("section");
  section.className = "repository-history";
  addText(section, "p", "BROWSER-LOCAL HISTORY", "discovery-label");
  addText(section, "h2", "Previously analyzed repositories");
  if (!savedConversations.length) {
    addText(section, "p", "No completed analyses are saved in this browser yet.", "empty-note");
  } else {
    const list = document.createElement("div");
    list.className = "history-list";
    savedConversations.forEach(record => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "history-item";
      button.dataset.conversationId = record.id;
      addText(button, "strong", record.workspace.repository.full_name);
      addText(button, "span", record.workspace.repository.description || "Repository analysis");
      addText(button, "small", `${record.messages.length} messages · commit ${record.workspace.repository.latest_commit.sha.slice(0, 12)}`);
      list.append(button);
    });
    section.append(list);
  }
  conversation.append(section);
  setProgress("Choose a saved repository to restore its conversation.", false);
}

document.querySelectorAll(".example-list a").forEach(example => example.addEventListener("click", event => {
  event.preventDefault();
  urlInput.value = example.dataset.repository || `https://github.com/${example.textContent.trim()}`;
  urlInput.focus();
  document.querySelector(".analysis-card").scrollIntoView({ behavior: "smooth", block: "center" });
}));

document.querySelectorAll("[data-new-repository]").forEach(link => link.addEventListener("click", event => {
  event.preventDefault();
  if (!isWorking) showHome();
}));
const backToHome = document.querySelector("#back-to-home");
if (backToHome) backToHome.addEventListener("click", () => { if (!isWorking) showHome(); });
document.querySelectorAll("[data-last-analysis]").forEach(link => link.addEventListener("click", event => {
  event.preventDefault();
  if (!isWorking) renderRepositoryHistory();
}));

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (isWorking) return;
  formError.textContent = "";
  if (!urlInput.value.trim()) {
    formError.textContent = "Enter a public GitHub repository URL.";
    urlInput.focus();
    return;
  }
  const requestId = ++activeRequest;
  const focus = instruction.value.trim();
  const mode = selectedMode();
  if (mode === "custom" && !focus) {
    formError.textContent = "Enter a question for Ask RepoPilot mode.";
    instruction.focus();
    return;
  }
  setWorking(true);
  followLatest = true;
  returnLatest.hidden = true;
  chatForm.hidden = true;
  conversation.replaceChildren();
  showWorkspace();
  setProgress("Starting repository analysis…");
  let workspaceData = null;
  let analysisTarget = null;
  let streamedText = "";
  let completed = false;
  try {
    await streamRequest("/api/analyze/stream", { url: urlInput.value.trim(), instruction: focus, mode }, (eventName, data) => {
      if (requestId !== activeRequest) return;
      if (eventName === "status") setProgress(data.message, true);
      if (eventName === "repository") {
        workspaceData = data;
        renderOverview(workspaceData);
        analysisTarget = startAnalysisConversation(data.repository, focus || (mode === "architecture" ? "Visualize the repository architecture." : "Provide the short repository overview."));
        setProgress("Selecting and retrieving relevant source files…", true);
      }
      if (eventName === "retrieval") setProgress(data.message, true);
      if (eventName === "context") setProgress(data.message, true);
      if (eventName === "token") {
        if (!analysisTarget) return;
        streamedText += data.text;
        analysisTarget.content.classList.remove("generating");
        renderStreamingText(analysisTarget.content, streamedText);
        followIfAppropriate();
      }
      if (eventName === "done") {
        if (!workspaceData || !analysisTarget) throw new Error("The analysis finished before repository information arrived.");
        const completeData = { ...workspaceData, ...data, analysis: data.analysis };
        lastCompletedAnalysis = completeData;
        activeSourceFiles = data.source_files || [];
        activeConversation = {
          id: data.conversation_id,
          workspace: compactWorkspace(completeData),
          mode,
          messages: [
            { role: "user", text: focus || "Provide the short repository overview." },
            { role: "assistant", ...data.analysis, coverage: data.retrieval_coverage },
          ],
          createdAt: new Date().toISOString(),
        };
        updateConversation(activeConversation);
        completeAnalysisConversation(analysisTarget, completeData);
        chatForm.hidden = false;
        setProgress(completeData.retrieval_coverage.complete ? "Analysis complete." : "Analysis complete with partial source coverage.", false);
        completed = true;
      }
      if (eventName === "error") throw new Error(data.message || "RepoPilot could not complete analysis.");
    });
    if (!completed) throw new Error("The connection ended before the analysis was complete.");
  } catch (error) {
    const message = error instanceof TypeError && /fetch/i.test(error.message)
      ? "RepoPilot server could not be reached. Start it with python3 app.py, then open http://127.0.0.1:5000."
      : error.message;
    const failure = document.createElement("article");
    failure.className = "message error";
    addText(failure, "p", "Unable to complete analysis", "message-label");
    addText(failure, "div", message, "message-content");
    if (analysisTarget) {
      analysisTarget.content.classList.remove("generating");
      analysisTarget.content.textContent = message;
      analysisTarget.content.classList.add("analysis-failed");
    } else conversation.append(failure);
    setProgress(message, false);
  } finally {
    setWorking(false);
  }
});

async function sendFollowUp(question, previousHistory, existingTarget = null) {
  if (!activeConversation || isWorking) return;
  const requestId = ++activeRequest;
  const text = question.trim();
  if (!text) return;
  const section = conversation.querySelector(".analysis-section");
  const target = existingTarget || (() => {
    appendConversationUser(section, text);
    return createFollowUpResponse();
  })();
  let streamedText = "";
  let completed = false;
  retryFollowUp = null;
  setWorking(true);
  followLatest = true;
  returnLatest.hidden = true;
  followUpQuestion.value = "";
  setProgress("Preparing follow-up analysis…", true);
  try {
    await streamRequest("/api/conversations/follow-up/stream", {
      conversation_id: activeConversation.id,
      question: text,
      history: previousHistory,
    }, (eventName, data) => {
      if (requestId !== activeRequest) return;
      if (eventName === "status") setProgress(data.message, true);
      if (eventName === "retrieval") setProgress(data.message, true);
      if (eventName === "context") setProgress(data.message, true);
      if (eventName === "token") {
        streamedText += data.text;
        target.content.classList.remove("generating");
        renderStreamingText(target.content, streamedText);
        followIfAppropriate();
      }
      if (eventName === "done") {
        const message = { role: "assistant", ...data.analysis, coverage: data.retrieval_coverage };
        mergeSourceFiles(data.source_files);
        target.content.classList.remove("generating");
        renderAnalysisResponse(target.content, data.analysis.text, activeSourceFiles);
        const meta = document.createElement("p");
        meta.className = "analysis-meta";
        meta.textContent = `${data.analysis.source_file_count} relevant files · commit ${data.analysis.commit_sha.slice(0, 12)}`;
        target.assistant.append(meta);
        const coverage = addText(target.assistant, "p", data.retrieval_coverage.message, "coverage-note");
        if (!data.retrieval_coverage.complete) coverage.classList.add("coverage-partial");
        activeConversation.messages.push({ role: "user", text });
        activeConversation.messages.push(message);
        updateConversation(activeConversation);
        setProgress(data.retrieval_coverage.complete ? "Follow-up complete." : "Follow-up complete with partial source coverage.", false);
        completed = true;
      }
      if (eventName === "error") throw new Error(data.message || "RepoPilot could not complete this follow-up.");
    });
    if (!completed) throw new Error("The connection ended before the follow-up was complete.");
  } catch (error) {
    target.content.classList.remove("generating");
    target.content.classList.add("analysis-failed");
    target.content.textContent = error.message;
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "retry-message";
    retry.textContent = "Retry message";
    target.assistant.append(retry);
    retryFollowUp = { question: text, history: previousHistory, target };
    setProgress(error.message, false);
  } finally {
    setWorking(false);
  }
}

chatForm.addEventListener("submit", event => {
  event.preventDefault();
  if (!activeConversation || isWorking) return;
  const question = followUpQuestion.value.trim();
  if (!question) {
    followUpQuestion.focus();
    return;
  }
  sendFollowUp(question, activeConversation.messages.slice());
});

window.addEventListener("scroll", () => { followLatest = isNearBottom(); }, { passive: true });
returnLatest.addEventListener("click", () => {
  followLatest = true;
  followIfAppropriate();
});
conversation.addEventListener("click", async event => {
  if (event.target.closest(".download-architecture-pdf")) {
    exportArchitecturePdf();
    return;
  }
  const historyItem = event.target.closest(".history-item");
  if (historyItem && !isWorking) {
    const record = savedConversations.find(item => item.id === historyItem.dataset.conversationId);
    if (record) restoreConversation(record);
    return;
  }
  const retry = event.target.closest(".retry-message");
  if (retry && retryFollowUp && !isWorking) {
    retry.remove();
    retryFollowUp.target.content.classList.remove("analysis-failed");
    setGeneratingState(retryFollowUp.target.content, "Retrying the follow-up analysis…");
    const failed = retryFollowUp;
    sendFollowUp(failed.question, failed.history, failed.target);
    return;
  }
  const reference = event.target.closest(".source-reference");
  if (reference) {
    showSourceFile(reference.dataset.path, activeSourceFiles);
    return;
  }
  const button = event.target.closest(".copy-code");
  if (!button) return;
  const code = button.nextElementSibling?.textContent || "";
  try {
    await navigator.clipboard.writeText(code);
    button.textContent = "Copied";
    setTimeout(() => { button.textContent = "Copy"; }, 1200);
  } catch {
    button.textContent = "Copy unavailable";
  }
});

document.querySelector(".theme-control").addEventListener("click", () => document.body.classList.toggle("dark-mode"));
