import { useEffect, useRef } from "react";
import { X, MapPin } from "lucide-react";

interface MapModalProps {
  storeName: string;
  mapUrl: string;
  unitNumber?: string;
  onClose: () => void;
}

export default function MapModal({
  storeName,
  mapUrl,
  unitNumber,
  onClose,
}: MapModalProps) {
  // Use Mappedin Web enterprise hash routing to navigate directly to the
  // location profile page, bypassing the home/search screen entirely.
  // Route: #/profile?location=<locationName|externalId>
  const iframeSrc = (() => {
    const baseUrl = mapUrl.replace(/#.*$/, "");
    const locationParam = encodeURIComponent(unitNumber || storeName);
    return `${baseUrl}#/profile?location=${locationParam}`;
  })();

  const iframeRef = useRef<HTMLIFrameElement>(null);

  // Close on Escape key
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  // Fallback: once Mappedin signals it is ready, also send a postMessage in case
  // the URL param was not processed (e.g. older viewer versions).
  useEffect(() => {
    const navigated = { current: false };
    function handleMessage(event: MessageEvent) {
      if (!event.data || typeof event.data !== "object") return;
      if (event.data.type === "app-loaded" && !navigated.current) {
        navigated.current = true;
        const win = iframeRef.current?.contentWindow;
        if (!win) return;
        win.postMessage(
          {
            type: "set-state",
            payload: {
              location: unitNumber || storeName,
            },
          },
          "*"
        );
      }
    }
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [storeName, unitNumber]);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col justify-end"
      role="dialog"
      aria-modal="true"
      aria-label={`Directions to ${storeName}`}
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Drawer panel */}
      <div className="relative z-10 flex h-[80vh] flex-col rounded-t-2xl bg-white shadow-2xl animate-slide-up">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
          <div className="flex items-center gap-2 min-w-0">
            <MapPin size={16} className="shrink-0 text-blue-600" />
            <span className="text-sm font-semibold text-gray-900 truncate">
              {storeName}
            </span>
          </div>
          <button
            onClick={onClose}
            className="ml-2 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-gray-400 transition hover:bg-gray-100 hover:text-gray-700"
            aria-label="Close map"
          >
            <X size={16} />
          </button>
        </div>

        {/* Map iframe */}
        <div className="flex-1 overflow-hidden">
          <iframe
            ref={iframeRef}
            src={iframeSrc}
            title={`Mall map — ${storeName}`}
            className="h-full w-full border-0"
            allow="fullscreen"
          />
        </div>
      </div>
    </div>
  );
}
