"use client";

import { useState, useEffect } from "react";
import {
  X,
  Bell,
  CheckCircle2,
  AlertTriangle,
  Info,
  Zap,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";

type NotificationType = "success" | "warning" | "info" | "alert";

interface Notification {
  id: string;
  type: NotificationType;
  title: string;
  message: string;
  time: string;
  read: boolean;
}

const INITIAL_NOTIFICATIONS: Notification[] = [
  {
    id: "n1",
    type: "success",
    title: "分析完成",
    message: "BTC 短期盤整分析報告已生成，置信度 82%。",
    time: "2 分鐘前",
    read: false,
  },
  {
    id: "n2",
    type: "alert",
    title: "價格警報",
    message: "ETH 突破 $3,400 阻力位，24H 漲幅 +1.62%。",
    time: "15 分鐘前",
    read: false,
  },
  {
    id: "n3",
    type: "warning",
    title: "資料源降級",
    message: "CoinGecko API Rate Limit 觸發，已自動切換備用數據源。",
    time: "1 小時前",
    read: true,
  },
  {
    id: "n4",
    type: "info",
    title: "系統更新",
    message: "ReAct Agent 已更新至 v2.1，新增 SOL DEX 交易量分析因子。",
    time: "3 小時前",
    read: true,
  },
  {
    id: "n5",
    type: "success",
    title: "歷史報告歸檔",
    message: "過去 24 小時共 3 份分析報告已自動存入 PostgreSQL。",
    time: "6 小時前",
    read: true,
  },
];

interface NotificationsPanelProps {
  open: boolean;
  onClose: () => void;
  onUnreadChange?: (count: number) => void;
}

export function NotificationsPanel({ open, onClose, onUnreadChange }: NotificationsPanelProps) {
  const [notifications, setNotifications] = useState<Notification[]>(INITIAL_NOTIFICATIONS);

  const unreadCount = notifications.filter((n) => !n.read).length;

  useEffect(() => {
    onUnreadChange?.(unreadCount);
  }, [unreadCount, onUnreadChange]);

  if (!open) return null;

  function markAllRead() {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  }

  function clearAll() {
    setNotifications([]);
  }

  function dismissNotification(id: string) {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  }

  const iconMap: Record<NotificationType, typeof CheckCircle2> = {
    success: CheckCircle2,
    warning: AlertTriangle,
    info: Info,
    alert: Zap,
  };

  const colorMap: Record<NotificationType, string> = {
    success: "text-emerald-600",
    warning: "text-amber-600",
    info: "text-blue-600",
    alert: "text-red-600",
  };

  const bgMap: Record<NotificationType, string> = {
    success: "bg-emerald-50",
    warning: "bg-amber-50",
    info: "bg-blue-50",
    alert: "bg-red-50",
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-end pt-14 pr-4 bg-black/20 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-full max-w-sm rounded-2xl border border-outline-variant bg-surface-container-lowest p-4 shadow-dropdown animate-fade-in max-h-[70vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-outline-variant pb-3 mb-3">
          <div className="flex items-center gap-2">
            <Bell className="h-4 w-4 text-primary" />
            <h3 className="text-[14px] font-bold text-primary">通知中心</h3>
            {unreadCount > 0 && (
              <span className="rounded-full bg-red-500 px-2 py-0.5 text-[10px] font-bold text-white">
                {unreadCount}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={markAllRead}
              className="rounded-md px-2 py-1 text-[10px] text-secondary hover:bg-surface-container-low transition-colors"
            >
              全部已讀
            </button>
            <button onClick={onClose} className="rounded-md p-1 text-secondary hover:bg-surface-container-low">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Notifications list */}
        <div className="overflow-y-auto chat-scroll flex-1 space-y-2">
          {notifications.length === 0 && (
            <div className="flex flex-col items-center gap-2 py-10 text-center">
              <Bell className="h-8 w-8 text-outline" />
              <p className="text-[12px] text-secondary">暫無通知</p>
            </div>
          )}

          {notifications.map((notification) => {
            const Icon = iconMap[notification.type];
            return (
              <div
                key={notification.id}
                className={`group relative rounded-xl border p-3 transition-colors ${
                  notification.read
                    ? "border-outline-variant/50 bg-surface-container-lowest"
                    : "border-primary/20 bg-surface-container-low"
                }`}
              >
                <div className="flex gap-2.5">
                  <div className={`flex-shrink-0 rounded-lg p-1.5 ${bgMap[notification.type]}`}>
                    <Icon className={`h-3.5 w-3.5 ${colorMap[notification.type]}`} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className={`text-[12px] font-semibold ${notification.read ? "text-secondary" : "text-primary"}`}>
                        {notification.title}
                      </span>
                      <span className="text-[10px] text-secondary flex-shrink-0">{notification.time}</span>
                    </div>
                    <p className="text-[11px] text-secondary leading-relaxed mt-0.5">
                      {notification.message}
                    </p>
                  </div>
                  <button
                    onClick={() => dismissNotification(notification.id)}
                    className="flex-shrink-0 rounded p-1 text-secondary opacity-0 group-hover:opacity-100 hover:text-red-500 transition-all"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
                {!notification.read && (
                  <div className="absolute top-3 right-3 h-2 w-2 rounded-full bg-primary" />
                )}
              </div>
            );
          })}
        </div>

        {/* Footer */}
        {notifications.length > 0 && (
          <div className="pt-3 border-t border-outline-variant mt-3">
            <button
              onClick={clearAll}
              className="flex items-center justify-center gap-1.5 w-full rounded-lg py-2 text-[11px] text-secondary hover:text-red-600 hover:bg-red-50 transition-colors"
            >
              <Trash2 className="h-3.5 w-3.5" />
              清除所有通知
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
