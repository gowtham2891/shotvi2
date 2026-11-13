// Data hooks for the Klipvi pages (React Query). Episodes refresh themselves every few seconds while one is running.
import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getEpisode, getMe, listCaptionTemplates, listEpisodes, submitEpisode } from "@/api/klipvi";

const ACTIVE = new Set(["waiting", "running"]);
const WS_KEY = "klipvi-workspace";

export const useMe = () => useQuery({ queryKey: ["me"], queryFn: getMe, staleTime: 15_000 });

/** The workspace the person is working in (remembered per browser), and a way to switch. */
export function useWorkspace() {
  const me = useMe();
  const [picked, setPicked] = useState(() => {
    try {
      return localStorage.getItem(WS_KEY);
    } catch {
      return null;
    }
  });
  const workspaces = me.data?.workspaces || [];
  const current = useMemo(
    () => workspaces.find((w) => w.id === picked) || workspaces[0] || null,
    [workspaces, picked],
  );
  const choose = useCallback((id) => {
    setPicked(id);
    try {
      localStorage.setItem(WS_KEY, id);
    } catch {
      /* private window: remembered for this visit only */
    }
  }, []);
  return { ...me, workspaces, workspace: current, choose };
}

export const useEpisodes = (workspaceId) =>
  useQuery({
    queryKey: ["episodes", workspaceId],
    queryFn: () => listEpisodes(workspaceId),
    enabled: Boolean(workspaceId),
    refetchInterval: (q) => ((q.state.data || []).some((e) => ACTIVE.has(e.status)) ? 5_000 : false),
  });

export const useEpisode = (episodeId) =>
  useQuery({
    queryKey: ["episode", episodeId],
    queryFn: () => getEpisode(episodeId),
    enabled: Boolean(episodeId),
    refetchInterval: (q) => (ACTIVE.has(q.state.data?.status) ? 5_000 : false),
  });

export function useSubmitEpisode(workspaceId) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (url) => submitEpisode(workspaceId, url),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["episodes", workspaceId] });
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

/** "Finding the clips (9 of 12)" -> { label: "Finding the clips", done: 9, total: 12 } */
export function parseStep(step) {
  const m = /^(.*)\((\d+) of (\d+)\)\s*$/.exec(step || "");
  return m ? { label: m[1].trim(), done: Number(m[2]), total: Number(m[3]) } : { label: step || "", done: 0, total: 0 };
}

export const youtubeThumb = (videoId) => (videoId ? `https://i.ytimg.com/vi/${videoId}/mqdefault.jpg` : null);

export const minutesLabel = (m) => `${Math.round(m)} min`;

export const useCaptionTemplates = (workspaceId) =>
  useQuery({
    queryKey: ["caption-templates", workspaceId],
    queryFn: () => listCaptionTemplates(workspaceId),
    staleTime: 5 * 60_000,
  });

/** "Word highlight: the spoken word turns yellow" -> { name: "Word highlight", hint: "the spoken word turns yellow" } */
export function templateLabel(t) {
  const [name, ...rest] = (t.label || t.name).split(":");
  return { name: name.trim(), hint: rest.join(":").trim() };
}

/** YouTube titles are often "Show || Episode - 2 || Cast || Channel": the first two parts make the title, the rest a
 *  small line under it. Titles without separators are kept whole. */
export function splitTitle(title) {
  const parts = (title || "").split(/\s*\|{1,2}\s*/).map((x) => x.trim()).filter(Boolean);
  if (parts.length <= 2) return { main: parts.join(", ") || title || "", rest: "" };
  return { main: parts.slice(0, 2).join(", "), rest: parts.slice(2).join(", ") };
}
