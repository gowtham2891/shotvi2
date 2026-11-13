import React from "react";
import { STATUS_META } from "@/data/mockData";

export const StatusBadge = ({ status, progress }) => {
  const meta = STATUS_META[status] || {
    label: status,
    color: "#a1a1aa",
  };
  const isProcessing = ["uploading", "transcribing", "selecting_clips", "exporting"].includes(
    status
  );
  return (
    <div
      className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full text-[11px] font-medium border"
      style={{
        color: meta.color,
        backgroundColor: `${meta.color}12`,
        borderColor: `${meta.color}30`,
      }}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${isProcessing ? "animate-pulse" : ""}`}
        style={{
          backgroundColor: meta.color,
          boxShadow: `0 0 8px ${meta.color}`,
        }}
      />
      <span className="tracking-wide">
        {meta.label}
        {typeof progress === "number" && isProcessing ? ` • ${progress}%` : ""}
      </span>
    </div>
  );
};

export default StatusBadge;
