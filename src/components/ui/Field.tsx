import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { cn } from "./cn";

interface FieldChromeProps {
  label: string;
  hint?: string;
  error?: string;
  optional?: boolean;
  className?: string;
}

function FieldChrome({
  label,
  hint,
  error,
  optional,
  className,
  children,
  inputId,
}: FieldChromeProps & { children: ReactNode; inputId: string }) {
  return (
    <div className={cn("grid min-w-0 gap-1.5", className)}>
      <div className="flex min-w-0 items-baseline justify-between gap-3">
        <label htmlFor={inputId} className="text-sm font-semibold text-ink">
          {label}
        </label>
        {optional && <span className="text-xs text-ink-faint">任意</span>}
      </div>
      {children}
      {(error || hint) && (
        <p
          id={`${inputId}-detail`}
          className={cn(
            "text-xs leading-relaxed",
            error ? "text-danger" : "text-ink-muted",
          )}
        >
          {error || hint}
        </p>
      )}
    </div>
  );
}

export interface FieldProps
  extends
    Omit<InputHTMLAttributes<HTMLInputElement>, "className">,
    FieldChromeProps {
  inputClassName?: string;
}

export const Field = forwardRef<HTMLInputElement, FieldProps>(function Field(
  { label, hint, error, optional, id, className, inputClassName, ...props },
  ref,
) {
  const generatedId = useId();
  const inputId = id || generatedId;
  const detailId = error || hint ? `${inputId}-detail` : undefined;

  return (
    <FieldChrome
      label={label}
      hint={hint}
      error={error}
      optional={optional}
      className={className}
      inputId={inputId}
    >
      <input
        ref={ref}
        id={inputId}
        className={cn("field", inputClassName)}
        aria-invalid={!!error || undefined}
        aria-describedby={detailId}
        {...props}
      />
    </FieldChrome>
  );
});

export interface TextAreaProps
  extends
    Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "className">,
    FieldChromeProps {
  inputClassName?: string;
}

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(
  function TextArea(
    { label, hint, error, optional, id, className, inputClassName, ...props },
    ref,
  ) {
    const generatedId = useId();
    const inputId = id || generatedId;
    const detailId = error || hint ? `${inputId}-detail` : undefined;

    return (
      <FieldChrome
        label={label}
        hint={hint}
        error={error}
        optional={optional}
        className={className}
        inputId={inputId}
      >
        <textarea
          ref={ref}
          id={inputId}
          className={cn("field min-h-24 resize-y", inputClassName)}
          aria-invalid={!!error || undefined}
          aria-describedby={detailId}
          {...props}
        />
      </FieldChrome>
    );
  },
);

export interface SelectFieldProps
  extends
    Omit<SelectHTMLAttributes<HTMLSelectElement>, "className">,
    FieldChromeProps {
  inputClassName?: string;
}

export const SelectField = forwardRef<HTMLSelectElement, SelectFieldProps>(
  function SelectField(
    {
      label,
      hint,
      error,
      optional,
      id,
      className,
      inputClassName,
      children,
      ...props
    },
    ref,
  ) {
    const generatedId = useId();
    const inputId = id || generatedId;
    const detailId = error || hint ? `${inputId}-detail` : undefined;

    return (
      <FieldChrome
        label={label}
        hint={hint}
        error={error}
        optional={optional}
        className={className}
        inputId={inputId}
      >
        <select
          ref={ref}
          id={inputId}
          className={cn("field", inputClassName)}
          aria-invalid={!!error || undefined}
          aria-describedby={detailId}
          {...props}
        >
          {children}
        </select>
      </FieldChrome>
    );
  },
);
