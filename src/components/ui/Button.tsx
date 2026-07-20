import { cva, type VariantProps } from "class-variance-authority";
import { LoaderCircle } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "./cn";

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 rounded-lg font-semibold leading-none transition-[color,background-color,border-color,box-shadow,transform] disabled:pointer-events-none disabled:opacity-50 active:translate-y-px motion-reduce:transform-none motion-reduce:transition-none",
  {
    variants: {
      variant: {
        primary:
          "border border-primary bg-primary text-white shadow-card hover:border-primary-hover hover:bg-primary-hover",
        secondary:
          "border border-line-strong bg-surface text-ink hover:border-primary/45 hover:bg-primary-soft/45",
        quiet:
          "border border-transparent bg-transparent text-ink-muted hover:border-line hover:bg-surface-muted hover:text-ink",
        cue: "border border-cue bg-cue text-white hover:border-cue-hover hover:bg-cue-hover",
        danger:
          "border border-danger bg-danger text-white hover:border-danger/90 hover:bg-danger/90",
      },
      size: {
        sm: "min-h-9 px-3 text-xs",
        md: "min-h-10 px-4 text-sm",
        lg: "min-h-11 px-5 text-sm",
        icon: "size-9 p-0",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps
  extends
    ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      className,
      children,
      disabled,
      loading = false,
      type = "button",
      variant,
      size,
      ...props
    },
    ref,
  ) {
    return (
      <button
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        disabled={disabled || loading}
        type={type}
        aria-busy={loading || undefined}
        {...props}
      >
        {loading && (
          <LoaderCircle
            aria-hidden="true"
            className="size-4 animate-spin motion-reduce:animate-none"
          />
        )}
        {children}
      </button>
    );
  },
);

export { buttonVariants };
