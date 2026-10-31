declare module "@novnc/novnc" {
  export type RFBDisconnectDetail = { clean: boolean };

  export default class RFB {
    constructor(target: HTMLElement, url: string, options?: Record<string, unknown>);
    viewOnly: boolean;
    scaleViewport: boolean;
    resizeSession: boolean;
    clipViewport: boolean;
    focusOnClick: boolean;
    background: string;
    disconnect(): void;
    sendCtrlAltDel(): void;
    clipboardPasteFrom(text: string): void;
    addEventListener(type: "connect", listener: () => void): void;
    addEventListener(type: "disconnect", listener: (event: CustomEvent<RFBDisconnectDetail>) => void): void;
    addEventListener(type: "securityfailure", listener: (event: CustomEvent<{ reason?: string }>) => void): void;
    addEventListener(type: "clipboard", listener: (event: CustomEvent<{ text: string }>) => void): void;
    removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void;
  }
}
