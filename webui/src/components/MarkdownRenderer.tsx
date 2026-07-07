import { useMemo, useCallback } from "react";
import { marked, Renderer } from "marked";
import DOMPurify from "dompurify";
import hljs from "highlight.js";
import "highlight.js/styles/github-dark.css";

// Configure marked with a custom renderer for code highlighting
const renderer = new Renderer();
renderer.code = function (code: string, infostring: string, _escaped: boolean) {
  const language = (infostring || "").match(/^\S*/)?.[0] || "";
  let highlighted = code;

  if (language && hljs.getLanguage(language)) {
    try {
      highlighted = hljs.highlight(code, { language }).value;
    } catch {
      // fall through - use raw text
    }
  } else if (!language) {
    try {
      highlighted = hljs.highlightAuto(code).value;
    } catch {
      // fall through - use raw text
    }
  }

  const langClass = language ? ` class="language-${language}"` : "";
  const codeId = `code-${Math.random().toString(36).slice(2, 9)}`;
  return `<div class="code-block"><div class="code-header"><span class="code-lang">${language || "code"}</span><button class="copy-btn" onclick="window.__copyCode('${codeId}')">Copy</button></div><pre><code id="${codeId}"${langClass}>${highlighted}</code></pre></div>`;
};

marked.setOptions({
  breaks: true,
  gfm: true,
});

// Use the custom renderer via use hook
marked.use({ renderer });

// Global copy function for code blocks
if (typeof window !== "undefined") {
  (window as any).__copyCode = (id: string) => {
    const el = document.getElementById(id);
    if (el) {
      navigator.clipboard.writeText(el.textContent || "").then(() => {
        const btn = el.closest(".code-block")?.querySelector(".copy-btn");
        if (btn) {
          btn.textContent = "Copied!";
          setTimeout(() => (btn.textContent = "Copy"), 2000);
        }
      });
    }
  };
}

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export function MarkdownRenderer({
  content,
  className = "",
}: MarkdownRendererProps) {
  const html = useMemo(() => {
    if (!content) return "";

    try {
      const rawHtml = marked.parse(content) as string;
      const sanitized = DOMPurify.sanitize(rawHtml, {
        ADD_ATTR: ["target", "onclick", "id"],
      });
      return sanitized;
    } catch {
      return `<p>${DOMPurify.sanitize(content)}</p>`;
    }
  }, [content]);

  return (
    <div
      className={`markdown-body ${className}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
