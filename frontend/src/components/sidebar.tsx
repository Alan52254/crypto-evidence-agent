"use client";

import {
  LayoutDashboard,
  History,
  FolderOpen,
  BarChart3,
  Settings,
  HelpCircle,
  User,
  Plus,
  X,
  ShieldCheck,
} from "lucide-react";
import { Button } from "@/components/ui/button";

export type NavTab = "workspace" | "history" | "library" | "reports" | "settings";

interface SidebarProps {
  open: boolean;
  activeTab: NavTab;
  onSelectTab: (tab: NavTab) => void;
  onClose: () => void;
  onNewAnalysis: () => void;
}

const NAV_ITEMS: { id: NavTab; icon: typeof LayoutDashboard; label: string }[] = [
  { id: "workspace", icon: LayoutDashboard, label: "Workspace" },
  { id: "history", icon: History, label: "History" },
  { id: "library", icon: FolderOpen, label: "Library" },
  { id: "reports", icon: BarChart3, label: "Reports" },
  { id: "settings", icon: Settings, label: "Settings" },
];

export function Sidebar({
  open,
  activeTab,
  onSelectTab,
  onClose,
  onNewAnalysis,
}: SidebarProps) {
  return (
    <>
      {/* Mobile overlay */}
      {open && (
        <div
          className="fixed inset-0 z-30 bg-black/20 backdrop-blur-sm md:hidden"
          onClick={onClose}
        />
      )}

      <nav
        className={`
          fixed inset-y-0 left-0 z-40 flex w-[230px] flex-col border-r border-outline-variant
          bg-surface transition-transform duration-200 ease-out
          md:relative md:translate-x-0
          ${open ? "translate-x-0" : "-translate-x-full"}
        `}
      >
        {/* Mobile close button */}
        <button
          onClick={onClose}
          className="absolute right-3 top-3 rounded-md p-1 text-secondary hover:bg-surface-container-low md:hidden"
          aria-label="Close sidebar"
        >
          <X className="h-5 w-5" />
        </button>

        {/* Brand Header */}
        <div className="flex items-center gap-3 px-4 py-4 border-b border-outline-variant/50">
          <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl bg-accent text-on-accent shadow-sm font-bold">
            <span className="text-[14px]">AI</span>
          </div>
          <div className="min-w-0">
            <h1 className="text-[15px] font-bold leading-tight text-primary">
              AlphaSonar
            </h1>
            <p className="text-[10px] text-secondary font-medium">HOYA BIT Research</p>
          </div>
        </div>

        {/* Navigation Items */}
        <div className="flex flex-1 flex-col gap-1 px-3 pt-3">
          {NAV_ITEMS.map(({ id, icon: Icon, label }) => {
            const isActive = activeTab === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => {
                  onSelectTab(id);
                  onClose();
                }}
                className={`flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-[13px] font-medium transition-all duration-150 text-left w-full
                  ${
                    isActive
                      ? "bg-accent text-on-accent shadow-sm font-semibold"
                      : "text-secondary hover:bg-surface-container-low hover:text-primary"
                  }`}
              >
                <Icon className="h-[17px] w-[17px] flex-shrink-0" />
                <span>{label}</span>
              </button>
            );
          })}
        </div>

        {/* New Analysis CTA Button */}
        <div className="px-3 pb-3">
          <Button
            onClick={() => {
              onSelectTab("workspace");
              onNewAnalysis();
              onClose();
            }}
            className="w-full gap-2 text-[12px] py-2.5"
          >
            <Plus className="h-4 w-4" />
            New Analysis
          </Button>
        </div>

        {/* User Account / Bottom Section with generous padding to prevent overlay occlusion */}
        <div className="flex flex-col gap-1 border-t border-outline-variant px-3 pt-3 pb-8">
          <button
            type="button"
            onClick={() => onSelectTab("settings")}
            className="flex items-center gap-2.5 rounded-xl px-2.5 py-2 text-[12px] text-secondary transition-colors hover:bg-surface-container-low hover:text-primary text-left"
          >
            <HelpCircle className="h-4 w-4 flex-shrink-0 text-secondary" />
            <span>Support & Docs</span>
          </button>

          <div className="mt-1 flex items-center gap-2.5 rounded-xl border border-outline-variant/60 bg-surface-container-lowest p-2.5 shadow-sm">
            <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-surface-container-high border border-outline-variant font-mono text-[11px] font-bold text-primary">
              AL
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-[12px] font-bold leading-none text-primary truncate">
                Alan Lin
              </p>
              <p className="text-[10px] text-secondary font-mono leading-tight truncate mt-0.5">
                Senior Analyst
              </p>
            </div>
          </div>
        </div>
      </nav>
    </>
  );
}
