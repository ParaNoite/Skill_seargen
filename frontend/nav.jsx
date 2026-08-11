import React, { useState } from "react";
import { createRoot } from "react-dom/client";

const views = [
  ["operations", "运营"],
  ["research", "研究"],
  ["results", "结果"],
];

function WorkspaceNav() {
  const [active, setActive] = useState("operations");

  function selectView(view) {
    setActive(view);
    window.dispatchEvent(new CustomEvent("workspace:view", { detail: { view } }));
  }

  return (
    <nav className="workspace-nav" aria-label="工作台导航">
      {views.map(([view, label]) => (
        <button
          className={`nav-tab${active === view ? " active" : ""}`}
          data-view={view}
          key={view}
          onClick={() => selectView(view)}
          type="button"
        >
          {label}
        </button>
      ))}
    </nav>
  );
}

const root = document.querySelector("#react-nav");
if (root) {
  createRoot(root).render(<WorkspaceNav />);
}
