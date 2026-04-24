import {
  RotateCcw,
  Download,
  Bug,
  BugOff,
  ChevronDown,
  MapPin,
} from "lucide-react";
import { MALLS } from "../lib/constants";

interface TopBarProps {
  tenantId: string;
  mallId: string;
  debugMode: boolean;
  sessionId: string | null;
  language: "en" | "ar";
  onTenantChange: (id: string) => void;
  onMallChange: (id: string) => void;
  onDebugToggle: (enabled: boolean) => void;
  onReset: () => void;
  onExport: () => void;
  onLanguageChange: (lang: "en" | "ar") => void;
}

export default function TopBar({
  mallId,
  debugMode,
  sessionId,
  language,
  onMallChange,
  onDebugToggle,
  onReset,
  onExport,
  onLanguageChange,
}: TopBarProps) {
  const activeMall = MALLS.find((m) => m.id === mallId) ?? MALLS[0];

  return (
    <header className="flex items-center justify-between border-b border-gray-200 bg-white px-4 py-2.5">
      <div className="flex items-center gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600 text-sm font-bold text-white">
          C
        </div>

        {/* Active Mall selector */}
        <div className="flex items-center gap-1.5">
          <span className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-blue-600">
            <MapPin size={10} />
            Active Mall
          </span>
          <MallSelector
            value={mallId}
            options={MALLS.map((m) => ({
              value: m.id,
              label: `${m.label} — ${m.city}`,
            }))}
            onChange={onMallChange}
          />
          <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-medium text-blue-600 ring-1 ring-blue-100">
            {activeMall.city}
          </span>
        </div>

        {sessionId && (
          <span className="rounded bg-gray-100 px-2 py-0.5 font-mono text-[10px] text-gray-400">
            {sessionId.slice(0, 16)}
          </span>
        )}
      </div>

      <div className="flex items-center gap-1.5">
        {/* Language toggle */}
        <button
          onClick={() => onLanguageChange(language === "en" ? "ar" : "en")}
          className="flex items-center gap-1 rounded-md border border-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-600 transition hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700"
          title={language === "en" ? "Switch to Arabic" : "التبديل إلى الإنجليزية"}
          dir="ltr"
        >
          {language === "en" ? "EN" : "عربي"}
          <span className="text-gray-300">|</span>
          {language === "en" ? "عربي" : "EN"}
        </button>

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

function MallSelector({
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
        className="appearance-none rounded-md border border-blue-200 bg-blue-50 py-1 pl-3 pr-7 text-xs font-semibold text-blue-700 focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <ChevronDown
        size={12}
        className="pointer-events-none absolute end-2 top-1/2 -translate-y-1/2 text-blue-400"
      />
    </div>
  );
}
