import { createPortal } from "react-dom";
import {
  createContext,
  useCallback,
  forwardRef,
  useContext,
  useEffect,
  useImperativeHandle,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react";
import { X } from "lucide-react";
import { cn } from "./cn";
import { Tooltip, TooltipPortalProvider } from "./Tooltip";

interface DialogContextValue {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const DialogContext = createContext<DialogContextValue | null>(null);
const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function useDialogContext(): DialogContextValue {
  const context = useContext(DialogContext);
  if (!context)
    throw new Error("Dialog components must be rendered inside Dialog");
  return context;
}

export function Dialog({
  open,
  onOpenChange,
  children,
}: DialogContextValue & { children: ReactNode }) {
  return (
    <DialogContext.Provider value={{ open, onOpenChange }}>
      {children}
    </DialogContext.Provider>
  );
}

export const DialogTrigger = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement>
>(function DialogTrigger({ onClick, ...props }, ref) {
  const { onOpenChange } = useDialogContext();
  return (
    <button
      ref={ref}
      type="button"
      onClick={(event) => {
        onClick?.(event);
        if (!event.defaultPrevented) onOpenChange(true);
      }}
      {...props}
    />
  );
});

export const DialogClose = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement>
>(function DialogClose({ onClick, ...props }, ref) {
  const { onOpenChange } = useDialogContext();
  return (
    <button
      ref={ref}
      type="button"
      onClick={(event) => {
        onClick?.(event);
        if (!event.defaultPrevented) onOpenChange(false);
      }}
      {...props}
    />
  );
});

export interface DialogContentProps extends ComponentPropsWithoutRef<"dialog"> {
  title: string;
  description?: string;
  children: ReactNode;
  showClose?: boolean;
  closeLabel?: string;
  onCloseAutoFocus?: (event: Event) => void;
  titleId?: string;
  descriptionId?: string;
  initialFocus?: "native" | "title";
  bodyClassName?: string;
}

export const DialogContent = forwardRef<HTMLDialogElement, DialogContentProps>(
  function DialogContent(
    {
      title,
      description,
      children,
      showClose = true,
      closeLabel = "閉じる",
      onCloseAutoFocus,
      initialFocus = "native",
      bodyClassName,
      titleId: explicitTitleId,
      descriptionId: explicitDescriptionId,
      className,
      onCancel,
      onClick,
      onKeyDown,
      ...props
    },
    forwardedRef,
  ) {
    const { open, onOpenChange } = useDialogContext();
    const generatedTitleId = useId();
    const generatedDescriptionId = useId();
    const titleId = explicitTitleId ?? generatedTitleId;
    const descriptionId = explicitDescriptionId ?? generatedDescriptionId;
    const dialogRef = useRef<HTMLDialogElement>(null);
    const [tooltipPortalContainer, setTooltipPortalContainer] =
      useState<HTMLDialogElement | null>(null);
    const setDialogElement = useCallback(
      (element: HTMLDialogElement | null) => {
        dialogRef.current = element;
        setTooltipPortalContainer(element);
      },
      [],
    );
    const titleRef = useRef<HTMLHeadingElement>(null);
    const wasOpenRef = useRef(open);
    const closeAutoFocusRef = useRef(onCloseAutoFocus);
    closeAutoFocusRef.current = onCloseAutoFocus;
    useImperativeHandle(
      forwardedRef,
      () => dialogRef.current as HTMLDialogElement,
    );

    useEffect(() => {
      const dialog = dialogRef.current;
      if (!dialog || !open) return;
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
      if (initialFocus === "title") titleRef.current?.focus();
      return () => {
        if (typeof dialog.close === "function") dialog.close();
        else dialog.removeAttribute("open");
      };
    }, [initialFocus, open]);
    useEffect(() => {
      if (wasOpenRef.current && !open) {
        onCloseAutoFocus?.(new Event("close", { cancelable: true }));
      }
      wasOpenRef.current = open;
    }, [onCloseAutoFocus, open]);
    useEffect(
      () => () => {
        if (wasOpenRef.current) {
          closeAutoFocusRef.current?.(new Event("close", { cancelable: true }));
        }
      },
      [],
    );

    if (!open) return null;

    return createPortal(
      <dialog
        ref={setDialogElement}
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        aria-modal="true"
        className={cn(
          "fixed left-1/2 top-1/2 z-50 m-0 flex max-h-[calc(100vh_-_2rem)] w-[calc(100vw_-_2rem)] max-w-[560px] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-[14px] border border-line bg-surface p-0 text-ink shadow-overlay backdrop:bg-ink/38 backdrop:backdrop-blur-[2px] focus:outline-none",
          className,
        )}
        onCancel={(event) => {
          onCancel?.(event);
          if (!event.defaultPrevented) {
            event.preventDefault();
            onOpenChange(false);
          }
        }}
        onKeyDown={(event) => {
          onKeyDown?.(event);
          if (event.defaultPrevented) return;
          if (event.key === "Escape") {
            event.preventDefault();
            onOpenChange(false);
            return;
          }
          if (event.key === "Tab") {
            const focusable = Array.from(
              event.currentTarget.querySelectorAll<HTMLElement>(
                FOCUSABLE_SELECTOR,
              ),
            );
            const first = focusable[0];
            const last = focusable.at(-1);
            if (
              first &&
              last &&
              ((event.shiftKey && document.activeElement === first) ||
                (!event.shiftKey && document.activeElement === last))
            ) {
              event.preventDefault();
              (event.shiftKey ? last : first).focus();
            }
          }
        }}
        onClick={(event) => {
          onClick?.(event);
          if (!event.defaultPrevented && event.target === event.currentTarget)
            onOpenChange(false);
        }}
        {...props}
      >
        <TooltipPortalProvider container={tooltipPortalContainer}>
          <header className="border-b border-line px-5 py-4 pr-14">
            <h2
              id={titleId}
              ref={titleRef}
              tabIndex={initialFocus === "title" ? -1 : undefined}
              className="font-display text-base font-bold tracking-[0.01em] text-ink focus:outline-none focus-visible:shadow-none"
            >
              {title}
            </h2>
            {description && (
              <p
                id={descriptionId}
                className="mt-1 text-xs leading-relaxed text-ink-muted"
              >
                {description}
              </p>
            )}
          </header>
          <div className={cn("min-h-0 flex-1 overflow-y-auto", bodyClassName)}>
            {children}
          </div>
          {showClose && (
            <Tooltip content={closeLabel}>
              <DialogClose
                aria-label={closeLabel}
                className="absolute right-3 top-3 inline-flex size-9 items-center justify-center rounded-lg text-ink-muted transition-colors hover:bg-surface-muted hover:text-ink"
              >
                <X aria-hidden="true" className="size-4" />
              </DialogClose>
            </Tooltip>
          )}
        </TooltipPortalProvider>
      </dialog>,
      document.body,
    );
  },
);
