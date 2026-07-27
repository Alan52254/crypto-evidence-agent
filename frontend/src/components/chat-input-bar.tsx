"use client";

import { FormEvent, useRef, useEffect } from "react";
import {
  Paperclip,
  Database,
  Terminal,
  ArrowUp,
  ChevronDown,
} from "lucide-react";
import type { Asset } from "@/lib/contracts";

interface ChatInputBarProps {
  asset: Asset;
  question: string;
  running: boolean;
  onAssetChange: (asset: Asset) => void;
  onQuestionChange: (question: string) => void;
  onSubmit: (event: FormEvent) => void;
}

const ASSETS: Asset[] = ["BTC", "ETH", "SOL", "BNB", "XRP"];

export function ChatInputBar({
  asset,
  question,
  running,
  onAssetChange,
  onQuestionChange,
  onSubmit,
}: ChatInputBarProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [question]);

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!running && question.trim()) {
        onSubmit(e as unknown as FormEvent);
      }
    }
  }

  return (
    <div className="input-gradient-mask pointer-events-none absolute bottom-0 left-0 w-full px-4 pb-5 pt-10 md:px-6">
      <form
        onSubmit={onSubmit}
        className="pointer-events-auto mx-auto max-w-3xl"
      >
        <div className="flex flex-col rounded-2xl border border-outline-variant bg-surface-container-lowest shadow-dropdown transition-all focus-within:border-primary focus-within:shadow-input-focus">
          {/* Textarea */}
          <textarea
            ref={textareaRef}
            value={question}
            onChange={(e) => onQuestionChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question about crypto markets, on-chain data, or risk analysis..."
            rows={1}
            maxLength={2000}
            className="w-full resize-none border-none bg-transparent px-4 pt-3.5 pb-1 text-body-md text-primary placeholder:text-secondary/60 focus:outline-none focus:ring-0"
          />

          {/* Action bar */}
          <div className="flex items-center justify-between px-2 pb-2">
            <div className="flex items-center gap-0.5">
              {/* Asset selector */}
              <div className="relative">
                <select
                  value={asset}
                  onChange={(e) => onAssetChange(e.target.value as Asset)}
                  className="appearance-none rounded-lg border-none bg-surface-container-low py-1.5 pl-3 pr-7 font-mono text-[12px] font-semibold text-primary transition-colors hover:bg-surface-container focus:outline-none focus:ring-0"
                >
                  {ASSETS.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
                <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-secondary" />
              </div>

              <div className="mx-1 h-5 w-px bg-outline-variant" />

              <button
                type="button"
                className="rounded-lg p-2 text-secondary transition-colors hover:bg-surface-container-low hover:text-primary"
                title="Upload File"
              >
                <Paperclip className="h-[18px] w-[18px]" />
              </button>

              <button
                type="button"
                className="rounded-lg p-2 text-secondary transition-colors hover:bg-surface-container-low hover:text-primary"
                title="Select Market Data"
              >
                <Database className="h-[18px] w-[18px]" />
              </button>

              <button
                type="button"
                className="rounded-lg p-2 text-secondary transition-colors hover:bg-surface-container-low hover:text-primary"
                title="Terminal Commands"
              >
                <Terminal className="h-[18px] w-[18px]" />
              </button>
            </div>

            {/* Send button */}
            <button
              type="submit"
              disabled={running || !question.trim()}
              className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-on-accent transition-all hover:brightness-95 active:scale-95 disabled:opacity-30"
              aria-label="Send analysis"
            >
              <ArrowUp className="h-[18px] w-[18px]" />
            </button>
          </div>
        </div>

        {/* Disclaimer */}
        <p className="mt-2 text-center font-mono text-[10px] text-secondary/70">
          AI 分析結果僅供參考，不構成投資建議。HOYA BIT 提供法幣信託隔離保障。
        </p>
      </form>
    </div>
  );
}
