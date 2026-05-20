"use client";

import { FormEvent, useEffect, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { api } from "@/lib/api";
import type { AIHistoryMessage } from "@/types/api";

const suggestedPrompts = [
  "Which jobs should I apply for first?",
  "What skills am I missing?",
  "How should I improve my CV?",
  "Which roles fit my profile best?"
];

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
};

export default function AIAdvisorPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [historyLoading, setHistoryLoading] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .aiHistory()
      .then((history) => {
        setMessages(history.map(historyMessageToChat));
        setError(null);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setHistoryLoading(false));
  }, []);

  const sendMessage = async (message: string) => {
    const trimmed = message.trim();
    if (!trimmed || loading) {
      return;
    }
    setMessages((current) => [...current, { role: "user", content: trimmed }]);
    setInput("");
    setLoading(true);
    setError(null);
    try {
      const result = await api.aiChat(trimmed);
      setMessages((current) => [...current, { role: "assistant", content: result.response }]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to contact AI advisor");
    } finally {
      setLoading(false);
    }
  };

  const clearChat = async () => {
    if (loading || historyLoading) {
      return;
    }
    setError(null);
    try {
      await api.clearAiHistory();
      setMessages([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to clear chat history");
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void sendMessage(input);
  };

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>AI Advisor</h2>
            <p className="muted-text">Ask questions about your CV, scraped jobs, and career direction.</p>
          </div>
          <button type="button" className="secondary-button compact-button" onClick={() => void clearChat()} disabled={loading || historyLoading || messages.length === 0}>
            Clear chat
          </button>
        </div>

        <div className="suggested-prompts">
          {suggestedPrompts.map((prompt) => (
            <button key={prompt} type="button" className="secondary-button compact-button" onClick={() => sendMessage(prompt)} disabled={loading}>
              {prompt}
            </button>
          ))}
        </div>

        <div className="chat-window" aria-live="polite">
          {historyLoading ? <LoadingState label="Loading chat history" /> : null}
          {!historyLoading && messages.length === 0 ? (
            <div className="chat-message assistant">
              <span>AI Advisor</span>
              <p>Ask about your saved CV/profile, scraped jobs, and career direction.</p>
            </div>
          ) : null}
          {messages.map((message, index) => (
            <div key={`${message.role}-${index}`} className={`chat-message ${message.role}`}>
              <span>{message.role === "user" ? "You" : "AI Advisor"}</span>
              <p>{message.content}</p>
            </div>
          ))}
          {loading ? <LoadingState label="AI Advisor is thinking" /> : null}
        </div>

        {error ? <ErrorState message={error} /> : null}

        <form className="chat-input-row" onSubmit={submit}>
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Ask about your CV, jobs, or next career move"
            disabled={loading}
          />
          <button type="submit" disabled={loading || !input.trim()}>
            Send
          </button>
        </form>
      </section>
    </div>
  );
}

function historyMessageToChat(message: AIHistoryMessage): ChatMessage {
  return {
    role: message.role === "assistant" ? "assistant" : "user",
    content: message.content
  };
}
