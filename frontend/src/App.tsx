import TopBar from "./components/TopBar";
import ChatPage from "./pages/ChatPage";
import DebugPanel from "./components/DebugPanel";
import { useChat } from "./hooks/useChat";

export default function App() {
  const chat = useChat();

  return (
    <div className="flex h-screen flex-col">
      <TopBar
        tenantId={chat.tenantId}
        mallId={chat.mallId}
        debugMode={chat.debugMode}
        sessionId={chat.sessionId}
        onTenantChange={chat.changeMallId}
        onMallChange={chat.changeMallId}
        onDebugToggle={chat.setDebugMode}
        onReset={chat.reset}
        onExport={chat.exportConversation}
      />

      <div className="flex flex-1 overflow-hidden">
        <div
          className={`flex flex-col ${chat.debugMode ? "w-1/2 min-w-0 lg:w-3/5" : "w-full"}`}
        >
          <ChatPage
            messages={chat.messages}
            isLoading={chat.isLoading}
            selectedTurnId={chat.selectedTurnId}
            onSend={chat.send}
            onSelectTurn={chat.setSelectedTurnId}
            onFeedback={chat.handleFeedback}
          />
        </div>

        {chat.debugMode && (
          <div className="w-1/2 min-w-0 border-l border-slate-700 lg:w-2/5">
            <DebugPanel
              selectedTurn={chat.selectedTurn}
              messages={chat.messages}
              selectedTurnId={chat.selectedTurnId}
              onSelectTurn={chat.setSelectedTurnId}
            />
          </div>
        )}
      </div>
    </div>
  );
}
