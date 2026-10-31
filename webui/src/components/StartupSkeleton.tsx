export type StartupSurface = "chat" | "settings";

/** Also inlined in index.html: keep document startup independent of app modules. */
export function readStartupLayout(): { surface: StartupSurface; settingsIndex: boolean } {
  const path = window.location.hash.slice(1).split("?", 2)[0];
  const settings = path === "/settings" || path.startsWith("/settings/");
  return { surface: settings ? "settings" : "chat", settingsIndex: path === "/settings" };
}

interface StartupSkeletonProps {
  surface?: StartupSurface;
  settingsIndex?: boolean;
  embedded?: boolean;
  label?: string;
}

function Block({ className = "" }: { className?: string }) {
  return <div className={`startup-block ${className}`} />;
}

/** One markup/CSS contract for the HTML first paint and React's loading boundaries. */
export function StartupSkeleton({
  surface = "chat", settingsIndex = false, embedded = false, label = "Loading Yoreni…",
}: StartupSkeletonProps) {
  const Root = embedded ? "section" : "main";
  return (
    <Root className="startup-shell" aria-busy="true" data-startup-surface={surface}
      data-startup-settings={surface === "settings" ? (settingsIndex ? "index" : "section") : undefined}
      data-embedded={embedded}>
      <span className="startup-status" role="status" data-boot-copy>{label}</span>
      <div className="startup-header" aria-hidden="true">
        <div className="startup-header-brand"><Block className="startup-header-mark" /><Block className="startup-header-word" /></div>
        <div className="startup-header-actions"><Block /><Block /><Block /></div>
      </div>
      <div className="startup-content" aria-hidden="true">
        <div className="startup-chat">
          <div className="startup-chat-layout">
            <div className="startup-welcome"><div className="startup-welcome-line"><Block /></div></div>
            <div className="startup-composer">
              <div className="startup-composer-surface">
                <div className="startup-composer-input"><Block /></div>
                <div className="startup-composer-footer">
                  <div className="startup-composer-primary"><Block className="startup-attach" /><Block className="startup-model" /></div>
                  <div className="startup-composer-actions"><Block className="startup-send" /></div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div className="startup-settings">
          <div className="startup-settings-index">
            <div className="startup-settings-profile">
              <Block className="startup-settings-avatar" />
              <div className="startup-settings-identity">
                <Block className="startup-settings-name" />
                <Block className="startup-settings-host" />
              </div>
            </div>
            {[0, 1].map((group) => (
              <div key={group} className="startup-settings-card">
                {[0, 1, 2, 3].map((row) => (
                  <div key={row} className="startup-settings-link">
                    <Block className="startup-settings-glyph" /><Block className="startup-settings-label" />
                    <Block className="startup-settings-chevron" />
                  </div>
                ))}
              </div>
            ))}
            <div className="startup-settings-card startup-settings-logout">
              <Block className="startup-settings-glyph" /><Block className="startup-settings-label" />
            </div>
          </div>
          <div className="startup-settings-section">
            <Block className="startup-settings-back" />
            <Block className="startup-settings-title" />
            {[0, 1, 2].map((group) => (
              <div key={group} className="startup-settings-group">
                <Block className="startup-settings-subtitle" />
                <div className="startup-settings-rows">
                  {[0, 1, 2].map((row) => (
                    <div key={row} className="startup-settings-row"><Block /><Block /></div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Root>
  );
}
