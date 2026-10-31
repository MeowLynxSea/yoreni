import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { EmojiPicker } from "@/components/projects/EmojiPicker";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export interface ProjectDraft {
  name: string;
  description: string;
  emoji: string;
}

const EMPTY_DRAFT: ProjectDraft = { name: "", description: "", emoji: "" };

interface ProjectDialogProps {
  open: boolean;
  mode: "create" | "edit";
  initial?: ProjectDraft;
  busy?: boolean;
  onCancel: () => void;
  onSubmit: (draft: ProjectDraft) => void;
}

export function ProjectDialog({
  open,
  mode,
  initial = EMPTY_DRAFT,
  busy = false,
  onCancel,
  onSubmit,
}: ProjectDialogProps) {
  const { t } = useTranslation();
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [emoji, setEmoji] = useState(initial.emoji);

  useEffect(() => {
    if (!open) return;
    setName(initial.name);
    setDescription(initial.description);
    setEmoji(initial.emoji);
  }, [open, initial.name, initial.description, initial.emoji]);

  const trimmed = name.trim();
  const creating = mode === "create";

  return (
    <Dialog open={open} onOpenChange={(next) => {
      if (!next) onCancel();
    }}>
      <DialogContent className="max-w-sm">
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!trimmed || busy) return;
            onSubmit({ name: trimmed, description: description.trim(), emoji: emoji.trim() });
          }}
        >
          <DialogHeader className="text-left">
            <DialogTitle>
              {t(creating ? "projects.createTitle" : "projects.editTitle")}
            </DialogTitle>
            <DialogDescription>
              {t(creating ? "projects.createDescription" : "projects.editDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-end gap-3">
            <div className="grid gap-1.5">
              <span className="text-[13px] text-muted-foreground">{t("projects.emoji")}</span>
              <EmojiPicker value={emoji} onChange={setEmoji} />
            </div>
            <label className="grid flex-1 gap-1.5 text-[13px] text-muted-foreground">
              {t("projects.name")}
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("projects.namePlaceholder")}
                maxLength={80}
                autoFocus
              />
            </label>
          </div>
          <label className="grid gap-1.5 text-[13px] text-muted-foreground">
            {t("projects.descriptionLabel")}
            <Textarea
              className="rounded-2xl"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder={t("projects.descriptionPlaceholder")}
              maxLength={2000}
              rows={3}
            />
          </label>
          <DialogFooter>
            <Button className="min-w-20" type="button" variant="outline" onClick={onCancel}>
              {t("common.cancel")}
            </Button>
            <Button className="min-w-20" type="submit" disabled={!trimmed || busy}>
              {t(creating ? "projects.createAction" : "projects.editAction")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
