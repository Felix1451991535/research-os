import type { ParseKeys } from "i18next";
import { useTranslation } from "react-i18next";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/misc";

const ROWS: [string[], ParseKeys][] = [
  [["g", "p"], "shortcuts.goProjects"], [["g", "l"], "shortcuts.goLibrary"], [["g", "r"], "shortcuts.goResearch"],
  [["g", "t"], "shortcuts.goTasks"], [["g", "s"], "shortcuts.goSettings"], [["n"], "shortcuts.newItem"], [["?"], "shortcuts.help"],
];

export function ShortcutHelp({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={t("shortcuts.title")} description={t("shortcuts.description")}>
        <ul className="grid gap-2">
          {ROWS.map(([keys, label]) => (
            <li key={label} className="flex items-center justify-between text-body-sm">
              <span>{t(label)}</span>
              <span className="flex gap-1">{keys.map((k) => <Kbd key={k}>{k}</Kbd>)}</span>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
