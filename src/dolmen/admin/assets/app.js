/* Build front end.
 *
 * Talks to the API in dolmen/admin/app.py. The preview iframe is just the dev
 * server's own output, so it live-reloads through the same SSE channel every
 * other page uses — the editor never has to push HTML into it.
 */
"use strict";

const api = {
  async tree() { return (await fetch("/_dolmen/api/tree")).json(); },
  async meta() { return (await fetch("/_dolmen/api/meta")).json(); },
  async read(path) {
    const r = await fetch("/_dolmen/api/file?path=" + encodeURIComponent(path));
    if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    return r.json();
  },
  async write(path, text) {
    const r = await fetch("/_dolmen/api/file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, text }),
    });
    if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    return r.json();
  },
  async create(payload) {
    const r = await fetch("/_dolmen/api/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    return r.json();
  },
  async build() {
    return (await fetch("/_dolmen/api/build", { method: "POST" })).json();
  },
  async upload(file) {
    const body = new FormData();
    body.append("file", file);
    const r = await fetch("/_dolmen/api/upload", { method: "POST", body });
    if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    return r.json();
  },
};

const el = (id) => document.getElementById(id);
const state = { editor: null, path: null, dirty: false, entries: [], meta: null };

/* ---------- chrome ---------- */

function toast(message, bad = false) {
  const node = el("toast");
  node.textContent = message;
  node.classList.toggle("bad", bad);
  node.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { node.hidden = true; }, bad ? 8000 : 2500);
}

function setStatus(text, kind = "") {
  const node = el("status");
  node.textContent = text;
  node.className = "status " + kind;
}

function setDirty(dirty) {
  state.dirty = dirty;
  el("save-btn").disabled = !dirty || !state.path;
  el("open-path").textContent = state.path
    ? state.path + (dirty ? " •" : "")
    : "No file open";
}

/* ---------- file tree ---------- */

const GROUP_ORDER = ["Content", "Posts", "Layouts", "Includes", "Data", "Plugins", "Assets", "Other"];

function groupFor(entry) {
  const top = entry.path.split("/")[0];
  if (top === "_posts" || top === "_drafts") return "Posts";
  if (top === "_layouts") return "Layouts";
  if (top === "_includes") return "Includes";
  if (top === "_data") return "Data";
  if (top === "_plugins") return "Plugins";
  if (top === "assets") return "Assets";
  if (entry.special || top.startsWith("_")) return "Content";
  if (entry.editable) return "Content";
  return "Other";
}

function renderTree() {
  const filter = el("filter").value.trim().toLowerCase();
  const groups = new Map();

  for (const entry of state.entries) {
    if (filter && !entry.path.toLowerCase().includes(filter)) continue;
    const name = groupFor(entry);
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(entry);
  }

  const tree = el("tree");
  tree.replaceChildren();

  for (const name of GROUP_ORDER) {
    const entries = groups.get(name);
    if (!entries || !entries.length) continue;

    const heading = document.createElement("div");
    heading.className = "group";
    heading.textContent = name;
    tree.append(heading);

    for (const entry of entries) {
      const button = document.createElement("button");
      button.className = "file";
      if (entry.special) button.classList.add("special");
      if (entry.image) button.classList.add("image");
      if (!entry.editable) button.classList.add("locked");
      if (entry.path === state.path) button.classList.add("active");
      button.title = entry.path;

      const dot = document.createElement("span");
      dot.className = "dot";
      const label = document.createElement("span");
      label.textContent = entry.path;
      button.append(dot, label);

      if (entry.editable) button.onclick = () => openFile(entry.path);
      tree.append(button);
    }
  }
}

async function refreshTree() {
  const data = await api.tree();
  state.entries = data.entries;
  el("site-name").textContent = data.root;
  renderTree();
}

/* ---------- editor ---------- */

const LANGUAGES = {
  md: "markdown", markdown: "markdown", html: "html", htm: "html",
  xml: "xml", json: "json", yml: "yaml", yaml: "yaml",
  css: "css", scss: "scss", js: "javascript", py: "python", txt: "plaintext",
};

async function openFile(path) {
  if (state.dirty && !confirm("Discard unsaved changes?")) return;

  let file;
  try {
    file = await api.read(path);
  } catch (error) {
    toast(String(error.message || error), true);
    return;
  }

  state.path = file.path;
  const language = LANGUAGES[path.split(".").pop().toLowerCase()] || "plaintext";
  const model = state.editor.getModel();
  monaco.editor.setModelLanguage(model, language);
  model.setValue(file.text);
  setDirty(false);
  renderTree();

  if (file.url) {
    el("preview-url").textContent = file.url;
    el("preview").src = file.url;
  }
}

async function saveFile() {
  if (!state.path || !state.dirty) return;
  setStatus("saving…");
  try {
    const result = await api.write(state.path, state.editor.getValue());
    setDirty(false);
    if (result.error) {
      setStatus("build failed", "bad");
      toast(result.error, true);
    } else {
      setStatus("saved", "ok");
      toast("Saved");
      refreshTree();
    }
  } catch (error) {
    setStatus("save failed", "bad");
    toast(String(error.message || error), true);
  }
}

/* ---------- image drop ---------- */

function wireDropZone() {
  const pane = document.querySelector(".editor-pane");
  const veil = el("drop-veil");
  let depth = 0;

  pane.addEventListener("dragenter", (event) => {
    if (!event.dataTransfer?.types.includes("Files")) return;
    event.preventDefault();
    depth += 1;
    veil.classList.add("on");
  });
  pane.addEventListener("dragover", (event) => {
    if (event.dataTransfer?.types.includes("Files")) event.preventDefault();
  });
  pane.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (!depth) veil.classList.remove("on");
  });
  pane.addEventListener("drop", async (event) => {
    if (!event.dataTransfer?.files?.length) return;
    event.preventDefault();
    depth = 0;
    veil.classList.remove("on");

    for (const file of event.dataTransfer.files) {
      setStatus("uploading " + file.name + "…");
      try {
        const result = await api.upload(file);
        insertAtCursor(result.markdown + "\n");
        setStatus("uploaded", "ok");
        toast(`Uploaded to ${result.url}`);
        refreshTree();
      } catch (error) {
        setStatus("upload failed", "bad");
        toast(String(error.message || error), true);
      }
    }
  });
}

function insertAtCursor(text) {
  const editor = state.editor;
  const selection = editor.getSelection();
  editor.executeEdits("upload", [{ range: selection, text, forceMoveMarkers: true }]);
  editor.focus();
}

/* ---------- new document ---------- */

async function wireNewDialog() {
  state.meta = await api.meta();

  const collection = el("new-collection");
  collection.replaceChildren();
  for (const name of state.meta.collections) {
    collection.append(new Option(name, name));
  }
  collection.append(new Option("pages", "pages"));
  collection.value = "posts";

  const layout = el("new-layout");
  layout.replaceChildren(new Option("(none)", ""));
  for (const name of state.meta.layouts) layout.append(new Option(name, name));

  el("new-btn").onclick = () => el("new-dialog").showModal();

  el("new-form").addEventListener("submit", async (event) => {
    const form = event.target;
    if (form.returnValue === "cancel") return;
    const data = new FormData(form);
    try {
      const result = await api.create({
        title: data.get("title"),
        collection: data.get("collection"),
        layout: data.get("layout"),
      });
      await refreshTree();
      await openFile(result.path);
      toast("Created " + result.path);
      form.reset();
    } catch (error) {
      toast(String(error.message || error), true);
    }
  });
}

/* ---------- boot ---------- */

require.config({
  paths: { vs: "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.52.2/min/vs" },
});

require(["vs/editor/editor.main"], async () => {
  state.editor = monaco.editor.create(el("editor"), {
    value: "",
    language: "markdown",
    theme: "vs-dark",
    automaticLayout: true,
    wordWrap: "on",
    minimap: { enabled: false },
    fontSize: 13,
    scrollBeyondLastLine: false,
    padding: { top: 12 },
  });

  state.editor.onDidChangeModelContent(() => {
    if (!state.dirty) setDirty(true);
  });
  state.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveFile);

  el("save-btn").onclick = saveFile;
  el("filter").oninput = renderTree;
  el("preview-open").onclick = () => window.open(el("preview").src, "_blank");
  el("build-btn").onclick = async () => {
    setStatus("building…");
    const result = await api.build();
    if (result.ok) {
      setStatus(`built ${result.documents} docs in ${result.duration}s`, "ok");
      if (result.warnings?.length) toast(`${result.warnings.length} warning(s)`, true);
    } else {
      setStatus("build failed", "bad");
      toast(result.error, true);
    }
    refreshTree();
  };

  window.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "s") {
      event.preventDefault();
      saveFile();
    }
  });
  window.addEventListener("beforeunload", (event) => {
    if (state.dirty) event.preventDefault();
  });

  wireDropZone();
  await refreshTree();
  await wireNewDialog();
  setStatus("ready", "ok");
});
