import { useDialog } from 'naive-ui';
export function useConfirm() {
  const dialog = useDialog();
  return (title: string, content: string) => new Promise<boolean>(resolve => {
    dialog.warning({title, content, positiveText: '确定', negativeText: '取消', onPositiveClick: () => resolve(true), onNegativeClick: () => resolve(false), onEsc: () => resolve(false), onClose: () => resolve(false), onMaskClick: () => resolve(false)});
  });
}
