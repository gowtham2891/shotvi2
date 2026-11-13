import "@/App.css";
import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";

import Landing from "@/pages/Landing";
import Auth from "@/pages/Auth";
import Episodes from "@/pages/Episodes";
import EpisodeClips from "@/pages/EpisodeClips";
import ClipEditor from "@/pages/ClipEditor";
import Editor from "@/pages/Editor";
import ExportPage from "@/pages/Export";
import { useAppStore } from "@/store/useAppStore";
import RequireAuth from "@/components/RequireAuth";

function App() {
  const initAuth = useAppStore((s) => s.initAuth);
  useEffect(() => initAuth(), [initAuth]);

  return (
    <div className="App bg-k-bg min-h-screen">
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/auth" element={<Auth />} />
          <Route path="/dashboard" element={<RequireAuth><Episodes /></RequireAuth>} />
          <Route path="/episodes/:episodeId" element={<RequireAuth><EpisodeClips /></RequireAuth>} />
          <Route path="/clips/:clipId/edit" element={<RequireAuth><ClipEditor /></RequireAuth>} />
          {/* the old upload and gallery pages talked to the old server: send people to Episodes */}
          <Route path="/upload" element={<Navigate to="/dashboard" replace />} />
          <Route path="/clips/:projectId" element={<Navigate to="/dashboard" replace />} />
          <Route path="/editor/:clipId" element={<RequireAuth><Editor /></RequireAuth>} />
          <Route path="/export/:clipId" element={<RequireAuth><ExportPage /></RequireAuth>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
      <Toaster position="bottom-right" richColors />
    </div>
  );
}

export default App;
