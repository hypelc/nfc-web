export type CancelledInlineEdit = {
  activeId: null;
  value: string;
};

export function isEscapeKey(key: string): boolean {
  return key === "Escape";
}

export function restoreInlineEdit(originalValue: string): CancelledInlineEdit {
  return { activeId: null, value: originalValue };
}
