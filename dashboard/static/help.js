/**
 * LEco DevOps Help & User Manual (/help)
 * Tree navigation, full-text search, markdown + Mermaid diagrams, internal help: links.
 */

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

let helpActiveId = null;
let helpSearchTimer = null;
let helpMermaidReady = false;

const MERMAID_BLOCK_RE = /```mermaid\n([\s\S]*?)```/g;

/** Extract mermaid blocks; replace with placeholders for marked + DOMPurify. */
function extractMermaidBlocks(markdown) {
  const blocks = [];
  const stripped = markdown.replace(MERMAID_BLOCK_RE, (_, code) => {
    const idx = blocks.length;
    blocks.push(code.trim());
    return `\n\n<p class="help-mermaid-slot" data-mermaid-idx="${idx}"></p>\n\n`;
  });
  return { stripped, blocks };
}

function initMermaidOnce() {
  if (helpMermaidReady || typeof mermaid === "undefined") return;
  mermaid.initialize({
    startOnLoad: false,
    theme: "dark",
    securityLevel: "loose",
    flowchart: { useMaxWidth: true, htmlLabels: true },
    sequence: { useMaxWidth: true },
  });
  helpMermaidReady = true;
}

/** Render .help-mermaid nodes after markdown is in the DOM. */
async function renderHelpMermaid(root) {
  if (!root) return;
  initMermaidOnce();
  const nodes = root.querySelectorAll(".help-mermaid");
  if (!nodes.length || typeof mermaid === "undefined") return;

  let i = 0;
  for (const el of nodes) {
    const src = (el.textContent || "").trim();
    if (!src) continue;
    const id = `help-mmd-${helpActiveId || "x"}-${i++}`;
    try {
      const { svg } = await mermaid.render(id, src);
      el.innerHTML = svg;
      el.classList.add("help-mermaid--done");
    } catch (err) {
      el.innerHTML = `<pre class="help-mermaid-error">${escapeHtml(String(err))}</pre>`;
      el.classList.add("help-mermaid--error");
    }
  }
}

/** Turn placeholder slots into .help-mermaid elements. */
function injectMermaidSlots(root, blocks) {
  root.querySelectorAll("[data-mermaid-idx]").forEach((slot) => {
    const idx = Number(slot.getAttribute("data-mermaid-idx"));
    const code = blocks[idx];
    if (code == null) return;
    const wrap = document.createElement("div");
    wrap.className = "help-diagram help-mermaid";
    wrap.setAttribute("role", "img");
    wrap.textContent = code;
    slot.replaceWith(wrap);
  });
}

function renderHelpTree(nodes, depth = 0) {
  if (!nodes || !nodes.length) return "";
  return (
    `<ul class="help-tree${depth ? " help-tree--nested" : ""}">` +
    nodes
      .map((node) => {
        const id = escapeHtml(node.id || "");
        const title = escapeHtml(node.title || "");
        const children = node.children;
        if (children && children.length) {
          return `<li class="help-tree__folder">
            <details class="help-tree__details" open>
              <summary class="help-tree__summary">${title}</summary>
              ${renderHelpTree(children, depth + 1)}
            </details>
          </li>`;
        }
        const active = id === helpActiveId ? " help-tree__link--active" : "";
        return `<li><button type="button" class="help-tree__link doc-link${active}" data-help-id="${id}">${title}</button></li>`;
      })
      .join("") +
    `</ul>`
  );
}

function setHelpSidebarActive(id) {
  helpActiveId = id;
  document.querySelectorAll("[data-help-id]").forEach((btn) => {
    btn.classList.toggle("help-tree__link--active", btn.getAttribute("data-help-id") === id);
    btn.classList.toggle("doc-link--active", btn.getAttribute("data-help-id") === id);
  });
}

function decorateHelpLinks(root) {
  if (!root) return;
  root.querySelectorAll('a[href^="help:"]').forEach((a) => {
    const id = (a.getAttribute("href") || "").replace(/^help:/, "");
    a.setAttribute("href", `#${id}`);
    a.classList.add("help-internal-link");
    a.addEventListener("click", (e) => {
      e.preventDefault();
      loadHelpTopic(id);
    });
  });
  if (typeof applyExternalLinkAttrs === "function") {
    applyExternalLinkAttrs(root);
  }
}

const LIVE_CATALOG_APIS = {
  "ecosystem-updates": "/api/ecosystem/updates",
  "llm-catalog-ollama": "/api/llm-catalog/ollama",
  "llm-catalog-airllm": "/api/llm-catalog/airllm",
};

function renderCatalogTable(models, backend) {
  if (!models?.length) return "<p class='muted'>No models in catalog.</p>";
  const rows = models
    .map((m) => {
      const name = escapeHtml(m.name || "");
      const pub = escapeHtml(m.publisher || "");
      const niche = escapeHtml((m.niche || []).join(", "));
      const spec = escapeHtml(m.specialty || "");
      const size = escapeHtml(m.size_disk || "");
      const inst = escapeHtml(m.install_cli || "");
      const flag = m.discovered_online ? " <span class='help-badge-new'>new</span>" : "";
      return `<tr>
        <td><code>${name}</code>${flag}</td>
        <td>${pub}</td>
        <td>${niche}</td>
        <td class="help-catalog-spec">${spec}</td>
        <td>${size}</td>
        <td><code class="help-catalog-cmd">${inst}</code></td>
      </tr>`;
    })
    .join("");
  return `<div class="help-catalog-wrap"><table class="help-catalog-table">
      <thead><tr>
        <th>Model</th><th>Publisher</th><th>Niche</th><th>Specialty</th><th>Size</th><th>Install</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
}

async function augmentLiveCatalogTopic(id, contentEl) {
  const api = LIVE_CATALOG_APIS[id];
  if (!api || !contentEl) return;
  try {
    const res = await fetch(api);
    const data = await res.json();
    const ts = escapeHtml(data.generated_at || "");
    const banner = document.createElement("div");
    banner.className = "help-live-banner";
    let summary = `Live data · generated <strong>${ts}</strong>`;
    if (id === "ecosystem-updates") {
      const n = data.service_updates_available || 0;
      const m = (data.model_alerts || []).length;
      summary += ` · <strong>${n}</strong> stack update(s) · <strong>${m}</strong> new Ollama alert(s)`;
    } else {
      summary += ` · <strong>${data.model_count || (data.models || []).length}</strong> models`;
    }
    banner.innerHTML = `${summary}
      <button type="button" class="ollama-act ollama-act--safe help-live-refresh">Refresh page</button>`;
    contentEl.prepend(banner);
    banner.querySelector(".help-live-refresh")?.addEventListener("click", () => loadHelpTopic(id, { pushState: false }));

    if (id === "llm-catalog-ollama" || id === "llm-catalog-airllm") {
      const host = document.createElement("div");
      host.className = "help-live-catalog-host";
      host.innerHTML = renderCatalogTable(data.models || [], data.backend || id);
      const tables = contentEl.querySelectorAll("table");
      if (tables.length) tables[tables.length - 1].replaceWith(host.firstElementChild || host);
      else contentEl.appendChild(host);
    }
  } catch (_) {
    /* markdown table from generated file is enough */
  }
}

function parseHelpMarkdown(raw) {
  const { stripped, blocks } = extractMermaidBlocks(raw);
  let html =
    typeof marked !== "undefined" && marked.parse
      ? marked.parse(stripped)
      : `<pre>${escapeHtml(raw)}</pre>`;
  if (typeof DOMPurify !== "undefined") {
    html = DOMPurify.sanitize(html, {
      ADD_TAGS: ["p"],
      ADD_ATTR: ["data-mermaid-idx", "class"],
    });
  }
  const mount = document.createElement("div");
  mount.innerHTML = html;
  injectMermaidSlots(mount, blocks);
  return mount.innerHTML;
}

async function loadHelpTopic(id, opts = {}) {
  const content = document.getElementById("helpContent");
  const toolbar = document.getElementById("helpToolbar");
  const crumb = document.getElementById("helpBreadcrumb");
  if (!content || !id) return;
  setHelpSidebarActive(id);
  if (opts.pushState !== false) {
    const url = new URL(window.location.href);
    url.searchParams.set("topic", id);
    window.history.replaceState({}, "", url);
  }
  content.innerHTML = "<p class='muted'>Loading…</p>";
  try {
    const res = await fetch(`/api/help/content?id=${encodeURIComponent(id)}`);
    const data = await res.json();
    if (!data.ok) {
      content.innerHTML = `<p class="muted">${escapeHtml(data.error || "Error")}</p>`;
      return;
    }
    const raw = data.markdown || "";
    content.innerHTML = parseHelpMarkdown(raw);
    content.classList.add("prose");
    decorateHelpLinks(content);
    await renderHelpMermaid(content);
    await augmentLiveCatalogTopic(id, content);
    if (crumb) crumb.textContent = data.breadcrumb || data.title || "";
    if (toolbar) {
      toolbar.innerHTML = `<span><strong>${escapeHtml(data.title || "")}</strong></span>
        <button type="button" id="helpReloadBtn" class="ollama-act ollama-act--safe">Reload</button>`;
      document.getElementById("helpReloadBtn")?.addEventListener("click", () =>
        loadHelpTopic(id, { pushState: false }),
      );
    }
    if (opts.hash) {
      const el = content.querySelector(opts.hash);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    } else if (!opts.keepScroll) {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  } catch (e) {
    content.innerHTML = `<p class="muted">${escapeHtml(String(e))}</p>`;
  }
}

async function loadHelpTree() {
  const sb = document.getElementById("helpSidebar");
  if (!sb) return;
  try {
    const res = await fetch("/api/help/tree");
    const data = await res.json();
    const tree = data.tree || [];
    sb.innerHTML = renderHelpTree(tree);
    sb.querySelectorAll("[data-help-id]").forEach((btn) => {
      btn.addEventListener("click", () => loadHelpTopic(btn.getAttribute("data-help-id")));
    });
    const qs = new URLSearchParams(window.location.search);
    const topic = qs.get("topic") || "welcome";
    await loadHelpTopic(topic, { pushState: false, keepScroll: true });
  } catch (e) {
    sb.innerHTML = `<p class="muted">${escapeHtml(String(e))}</p>`;
  }
}

async function runHelpSearch(q) {
  const box = document.getElementById("helpSearchResults");
  if (!box) return;
  const term = (q || "").trim();
  if (term.length < 2) {
    box.classList.add("is-hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("is-hidden");
  box.innerHTML = "<p class='muted small'>Searching…</p>";
  try {
    const res = await fetch(`/api/help/search?q=${encodeURIComponent(term)}`);
    const data = await res.json();
    const results = data.results || [];
    if (!results.length) {
      box.innerHTML = "<p class='muted small'>No matches.</p>";
      return;
    }
    box.innerHTML = results
      .map(
        (r) => `<button type="button" class="help-search-hit" data-help-id="${escapeHtml(r.id)}">
          <strong>${escapeHtml(r.title)}</strong>
          <span class="muted small">${escapeHtml(r.breadcrumb || "")}</span>
          <span class="help-search-snippet">${escapeHtml(r.snippet || "")}</span>
        </button>`,
      )
      .join("");
    box.querySelectorAll("[data-help-id]").forEach((btn) => {
      btn.addEventListener("click", () => {
        loadHelpTopic(btn.getAttribute("data-help-id"));
        box.classList.add("is-hidden");
      });
    });
  } catch (e) {
    box.innerHTML = `<p class="muted">${escapeHtml(String(e))}</p>`;
  }
}

function initHelpPage() {
  initMermaidOnce();
  if (typeof applyExternalLinkAttrs === "function") {
    applyExternalLinkAttrs();
  }
  loadHelpTree();
  const inp = document.getElementById("helpSearchInput");
  const clearBtn = document.getElementById("helpSearchClear");
  inp?.addEventListener("input", () => {
    clearTimeout(helpSearchTimer);
    helpSearchTimer = setTimeout(() => runHelpSearch(inp.value), 280);
  });
  clearBtn?.addEventListener("click", () => {
    if (inp) inp.value = "";
    runHelpSearch("");
  });
}

document.addEventListener("DOMContentLoaded", initHelpPage);
