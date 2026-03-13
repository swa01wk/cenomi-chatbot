import {
  RotateCcw,
  Download,
  Bug,
  BugOff,
  ChevronDown,
} from "lucide-react";
import { TENANTS, MALLS } from "../lib/constants";

interface TopBarProps {
  tenantId: string;
  mallId: string;
  debugMode: boolean;
  sessionId: string | null;
  onTenantChange: (id: string) => void;
  onMallChange: (id: string) => void;
  onDebugToggle: (enabled: boolean) => void;
  onReset: () => void;
  onExport: () => void;
}

export default function TopBar({
  tenantId,
  mallId,
  debugMode,
  sessionId,
  onTenantChange,
  onMallChange,
  onDebugToggle,
  onReset,
  onExport,
}: TopBarProps) {
  return (
    <header className="flex items-center justify-between border-b border-gray-200 bg-white px-4 py-2.5">
      <div className="flex items-center gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600 text-sm font-bold text-white">
          C
        </div>
        <div className="flex items-center gap-2">
          <Selector
            value={tenantId}
            options={TENANTS.map((t) => ({ value: t.id, label: t.label }))}
            onChange={onTenantChange}
          />
          <span className="text-gray-300">/</span>
          <Selector
            value={mallId}
            options={MALLS.map((m) => ({ value: m.id, label: m.label }))}
            onChange={onMallChange}
          />
        </div>
        {sessionId && (
          <span className="rounded bg-gray-100 px-2 py-0.5 font-mono text-[10px] text-gray-400">
            {sessionId.slice(0, 16)}
          </span>
        )}
      </div>

      <div className="flex items-center gap-1.5">
        <button
          onClick={() => onDebugToggle(!debugMode)}
          className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition ${
            debugMode
              ? "bg-amber-50 text-amber-700 ring-1 ring-amber-200"
              : "text-gray-500 hover:bg-gray-100"
          }`}
          title={debugMode ? "Hide debug panel" : "Show debug panel"}
        >
          {debugMode ? <BugOff size={14} /> : <Bug size={14} />}
          {debugMode ? "Debug On" : "Debug"}
        </button>
        <button
          onClick={onExport}
          className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-gray-500 transition hover:bg-gray-100"
          title="Export conversation"
        >
          <Download size={14} />
          Export
        </button>
        <button
          onClick={onReset}
          className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-red-500 transition hover:bg-red-50"
          title="Reset session"
        >
          <RotateCcw size={14} />
          Reset
        </button>
      </div>
    </header>
  );
}

function Selector({
  value,
  options,
  onChange,
}: {
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (v: string) => void;
}) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="appearance-none rounded-md border border-gray-200 bg-gray-50 py-1 pl-3 pr-7 text-xs font-medium text-gray-700 focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <ChevronDown
        size={12}
        className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-gray-400"
      />
    </div>
  );
}
