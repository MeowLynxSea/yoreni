import { useState } from "react";
import { Lock } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import type { CredentialAuthRequest } from "@/lib/types";
import { cn } from "@/lib/utils";

const NON_SECRET_FIELDS = new Set(["username", "user", "email", "label"]);

const FIELD_LABEL_KEYS: Record<string, string> = {
  username: "auth.fields.username",
  user: "auth.fields.username",
  email: "auth.fields.email",
  password: "auth.fields.password",
  api_key: "auth.fields.apiKey",
  code: "auth.fields.code",
};

const CANCEL_CLASS = cn(
  "h-9 rounded-full px-4 text-[13px] text-muted-foreground transition-colors",
  "hover:bg-muted/60 hover:text-foreground disabled:opacity-50",
);

const SUBMIT_CLASS = cn(
  "h-9 rounded-full bg-primary px-5 text-[13px] font-medium text-primary-foreground",
  "shadow-[0_1px_2px_rgba(20,28,42,0.12)] transition-[background-color,opacity] hover:bg-primary/88",
  "active:scale-95 motion-reduce:active:scale-100 disabled:opacity-50",
);

function humanize(field: string): string {
  return field
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function labelFor(field: string, t: TFunction): string {
  const key = FIELD_LABEL_KEYS[field];
  return key ? t(key) : humanize(field);
}

export function AuthenticationCard({
  request,
  busy,
  onCancel,
  onSubmit,
  onOAuth,
}: {
  request: CredentialAuthRequest;
  busy: boolean;
  onCancel: (requestId: string) => void;
  onSubmit: (requestId: string, values: Record<string, string>) => void;
  onOAuth: (request: CredentialAuthRequest) => void;
}) {
  const { t } = useTranslation();
  const [values, setValues] = useState<Record<string, string>>({});
  const isOAuth = request.method === "oauth2";
  const fields = request.fields.length ? request.fields : ["api_key"];

  return (
    <form
      className={cn(
        "app-surface-glass w-full rounded-[1.6rem] px-5 py-4 text-reading",
        "animate-in fade-in-0 slide-in-from-bottom-1 motion-reduce:animate-none sm:px-6 sm:py-5",
      )}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(request.id, values);
      }}
    >
      <p className="text-[15px] leading-6 text-foreground">
        {request.purpose || t("auth.title")}
      </p>
      {request.allowed_domains?.length ? (
        <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[12px] text-muted-foreground">
          <span>{t("auth.scope")}</span>
          {request.allowed_domains.map((domain) => (
            <span
              key={domain}
              className="rounded-full border border-border/55 px-2 py-0.5 text-[11px] text-foreground/80"
            >
              {domain}
            </span>
          ))}
        </p>
      ) : null}

      {isOAuth ? (
        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            type="button"
            className={CANCEL_CLASS}
            onClick={() => onCancel(request.id)}
            disabled={busy}
          >
            {t("auth.cancel")}
          </button>
          <button
            type="button"
            className={SUBMIT_CLASS}
            onClick={() => onOAuth(request)}
            disabled={busy}
          >
            {t("auth.continueInBrowser")}
          </button>
        </div>
      ) : (
        <>
          <div className="mt-4 grid gap-3">
            {fields.map((field) => (
              <label key={field} className="grid gap-1.5">
                <span className="text-[12px] text-muted-foreground">
                  {labelFor(field, t)}
                </span>
                <input
                  className={cn(
                    "h-10 rounded-[0.9rem] border border-border/55 bg-background/45 px-3.5 text-[13px] text-foreground",
                    "outline-none transition-colors focus-visible:border-foreground/20 focus-visible:bg-background/70",
                  )}
                  type={NON_SECRET_FIELDS.has(field) ? "text" : "password"}
                  autoComplete="off"
                  value={values[field] ?? ""}
                  onChange={(event) =>
                    setValues((current) => ({ ...current, [field]: event.target.value }))
                  }
                />
              </label>
            ))}
          </div>
          <div className="mt-4 flex items-center justify-end gap-2">
            <button
              type="button"
              className={CANCEL_CLASS}
              onClick={() => onCancel(request.id)}
              disabled={busy}
            >
              {t("auth.cancel")}
            </button>
            <button type="submit" className={SUBMIT_CLASS} disabled={busy}>
              {t("auth.connect")}
            </button>
          </div>
        </>
      )}

      <p className="mt-3 flex items-start gap-1.5 text-[11px] leading-4 text-muted-foreground">
        <Lock className="mt-0.5 size-3 shrink-0" strokeWidth={1.75} aria-hidden />
        {t("auth.secureNote")}
      </p>
    </form>
  );
}
