import * as React from "react";
import { cn } from "@/lib/utils";

type ButtonVariant = "primary" | "secondary" | "ghost";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

const variantStyles: Record<ButtonVariant, string> = {
  primary:
    "bg-primary text-on-primary hover:bg-primary/90 active:scale-[0.97]",
  secondary:
    "bg-surface-container-lowest border border-outline-variant text-primary hover:bg-surface-container-low active:scale-[0.97]",
  ghost:
    "bg-transparent text-secondary hover:text-primary hover:bg-surface-container-low",
};

export function Button({
  className,
  variant = "primary",
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex min-h-[40px] items-center justify-center gap-2 rounded-lg px-4 text-label-caps font-semibold tracking-wide transition-all duration-150 disabled:cursor-not-allowed disabled:opacity-40",
        variantStyles[variant],
        className,
      )}
      {...props}
    />
  );
}
