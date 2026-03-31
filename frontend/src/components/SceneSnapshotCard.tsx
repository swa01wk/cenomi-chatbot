import React from "react";

interface SceneSnapshotCardProps {
  scene: Record<string, unknown>;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function formatValue(value: unknown): React.ReactNode {
  if (Array.isArray(value)) {
    if (value.length === 0) return null;
    if (value.every((item) => !isPlainObject(item))) {
      return <span className="text-slate-200">{value.join(", ")}</span>;
    }
    return (
      <div className="ml-2 space-y-1">
        {value.map((item, i) => (
          <div key={i} className="rounded bg-slate-800 px-2 py-1">
            {isPlainObject(item)
              ? Object.entries(item)
                  .filter(
                    ([, v]) =>
                      v !== null &&
                      v !== undefined &&
                      v !== "" &&
                      !(Array.isArray(v) && v.length === 0),
                  )
                  .map(([k, v]) => (
                    <div key={k} className="flex gap-1">
                      <span className="text-slate-400">{k.replace(/_/g, " ")}:</span>
                      <span className="text-slate-200">{String(v)}</span>
                    </div>
                  ))
              : String(item)}
          </div>
        ))}
      </div>
    );
  }

  if (isPlainObject(value)) {
    const subEntries = Object.entries(value).filter(
      ([, v]) =>
        v !== null &&
        v !== undefined &&
        v !== "" &&
        !(Array.isArray(v) && v.length === 0),
    );
    if (subEntries.length === 0) return null;
    return (
      <div className="ml-2 space-y-0.5 rounded bg-slate-800 px-2 py-1">
        {subEntries.map(([k, v]) => (
          <div key={k} className="flex gap-1 text-xs">
            <span className="shrink-0 text-slate-400">{k.replace(/_/g, " ")}:</span>
            <span className="text-slate-200">{String(v)}</span>
          </div>
        ))}
      </div>
    );
  }

  return <span className="text-slate-200">{String(value)}</span>;
}

export default function SceneSnapshotCard({ scene }: SceneSnapshotCardProps) {
  const hasContent = (v: unknown): boolean => {
    if (v === "" || v === null || v === undefined) return false;
    if (Array.isArray(v)) return v.length > 0 && v.some(hasContent);
    if (isPlainObject(v)) return Object.values(v).some(hasContent);
    return true;
  };

  const entries = Object.entries(scene).filter(([, v]) => hasContent(v));

  if (entries.length === 0) {
    return (
      <p className="text-xs italic text-slate-500">No scene data captured</p>
    );
  }

  return (
    <div className="space-y-1.5">
      {entries.map(([key, value]) => (
        <div key={key} className="flex items-start gap-2 text-xs">
          <span className="shrink-0 font-medium text-slate-400">
            {key.replace(/_/g, " ")}
          </span>
          {formatValue(value)}
        </div>
      ))}
    </div>
  );
}
