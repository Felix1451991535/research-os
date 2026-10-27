import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import type { ReactNode } from "react";

export async function renderPage(page: ReactNode, path: string, url: string) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); });
  await act(async () => { root.render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={[url]}>
    <Routes><Route path={path} element={page} /><Route path="*" element={<p>Destination</p>} /></Routes>
  </MemoryRouter></QueryClientProvider>); });
  await flush();
  return {
    host, flush,
    async click(text: string) {
      // Poll for an ENABLED button instead of one fixed delay: under a loaded full-suite run, queries may still be
      // loading (button disabled, the click would be ignored) or a dialog may render after 30 ms.
      const find = () => [...document.querySelectorAll("button")].find((b) => b.textContent === text && !b.disabled);
      let button = find();
      for (let waited = 0; !button && waited < 2000; waited += 30) { await flush(); button = find(); }
      if (!button) throw new Error(`Missing button: ${text}`);
      await act(async () => button.click()); await flush();
    },
    async fill(label: string, value: string) {
      const el = document.getElementById(label) as HTMLInputElement | HTMLTextAreaElement;
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      await act(async () => {
        const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
        if (!setter) throw new Error(`Missing input setter: ${label}`);
        setter.call(el, value);
        el.dispatchEvent(new Event("input", { bubbles: true }));
      });
    },
    async dispose() { await act(async () => root.unmount()); qc.clear(); host.remove(); },
  };
}

export const revision = { plan_id: "p", revision: 1, content_hash: "hash", approved_by_revision: null,
  content: { hypothesis: "A test hypothesis", template: { id: "smoke", hash: "tpl-hash" }, params: {}, metrics: ["score"], limits: { max_minutes: 5 }, split: { test: "sealed" } } };
export const study = { id: "s", plan_id: "p", current_revision: 1, base_revision: 1, stage: "search", configs_evaluated: 12,
  protocol: { objective: "score", factors: ["lr", "batch"] }, data: { screen: { anova: { effects: [
    { term: "lr", reported: true, p: 0.001, p_holm: 0.003, partial_eta2: 0.6, statement: "effect within this setup" },
    { term: "lr:batch", reported: false, p: null, p_holm: null, partial_eta2: null, statement: "NOT ESTIMABLE: missing corner" },
  ], curvature: null, sigma: 0.02, notes: ["missing design cells"] },
  failure_rules: [{ rule: "lr > 0.3", failed: 6, support: 6, purity: 1 }] } } };
