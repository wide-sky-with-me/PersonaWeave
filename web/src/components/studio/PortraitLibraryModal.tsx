import * as Dialog from '@radix-ui/react-dialog';
import React, { useEffect, useMemo, useState } from 'react';
import { Check, Library, Loader2, Search, User, Users, X } from 'lucide-react';
import { api } from '@/services/api';
import { useStudioStore } from '@/stores/useStudioStore';
import { useToast } from '@/components/ui/ToastProvider';
import type { PortraitAssetItem, PortraitLibraryData, PortraitLibraryIdentity } from '@/types';
import { describePortraitYaw, portraitYawFromLabel, selectDiversePortraitAssets } from '@/utils/portraitAssetSelection';
import { portraitAssetThumbnailUrl } from '@/utils/sourceReferences';

export const PortraitLibraryModal: React.FC<{ open: boolean; onClose: () => void }> = ({ open, onClose }) => {
  const store = useStudioStore();
  const toast = useToast();
  const [catalog, setCatalog] = useState<PortraitLibraryData | null>(null);
  const [gender, setGender] = useState<PortraitLibraryIdentity['gender'] | 'all'>('all');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<PortraitLibraryIdentity | null>(null);
  const [selectedAssetIds, setSelectedAssetIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    setSelected(null);
    setSelectedAssetIds(new Set());
    api
      .getPortraitLibrary()
      .then(setCatalog)
      .catch((reason) => {
        const message = reason?.message || '无法读取本地素材库';
        setError(message);
        toast({ title: '素材库读取失败', description: message, kind: 'error' });
      })
      .finally(() => setLoading(false));
  }, [open, toast]);

  const identities = useMemo(
    () =>
      (catalog?.identities || []).filter((identity) => {
        if (gender !== 'all' && identity.gender !== gender) return false;
        const keyword = query.trim().toLowerCase();
        return !keyword || identity.code.toLowerCase().includes(keyword) || String(identity.age).includes(keyword);
      }),
    [catalog, gender, query],
  );

  const chooseIdentity = (identity: PortraitLibraryIdentity) => {
    setSelected(identity);
    setSelectedAssetIds(new Set(selectDiversePortraitAssets(identity.assets).map((asset) => asset.asset_id)));
  };

  const toggleAsset = (asset: PortraitAssetItem) =>
    setSelectedAssetIds((current) => {
      const next = new Set(current);
      if (next.has(asset.asset_id)) next.delete(asset.asset_id);
      else if (next.size < 8) next.add(asset.asset_id);
      return next;
    });

  const importIdentity = () => {
    if (!selected || selectedAssetIds.size === 0) return;
    const assets = selected.assets.filter((asset) => selectedAssetIds.has(asset.asset_id));
    let personId: number | null = null;
    let imported = 0;
    try {
      personId = store.addSourcePerson(selected.display_name, selected.id);
      assets.forEach((asset) => {
        try {
          const yaw = portraitYawFromLabel(asset.label);
          store.addSourceReference({
            id: 'library_' + selected.id + '_' + asset.asset_id,
            name: asset.name,
            kind: 'managed',
            assetId: asset.asset_id,
            facePreview: portraitAssetThumbnailUrl(asset.asset_id),
            yaw,
            yawLabel: describePortraitYaw(yaw),
            personId: personId as number,
          });
          imported += 1;
        } catch {
          /* Continue so partial imports are reported to the user. */
        }
      });
      if (imported === 0) {
        store.removeSourcePerson(personId);
        toast({ title: '素材导入失败', description: '未能添加所选照片，请重试。', kind: 'error' });
        return;
      }
      store.setActiveSourcePersonId(personId);
      toast({
        title: imported === assets.length ? '素材已导入' : '部分素材导入失败',
        description: `已导入 ${imported} / ${assets.length} 张照片。`,
        kind: imported === assets.length ? 'success' : 'error',
      });
      onClose();
    } catch (reason) {
      if (personId !== null && imported === 0) store.removeSourcePerson(personId);
      toast({
        title: '素材导入失败',
        description: reason instanceof Error ? reason.message : '请稍后重试。',
        kind: 'error',
      });
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-black/75 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-[81] flex h-[min(820px,96vh)] w-[calc(100%-1rem)] max-w-6xl -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-surface-2 shadow-elevated sm:h-[min(820px,92vh)] sm:w-[calc(100%-2rem)]">
          <header className="flex items-start justify-between gap-3 border-b border-border px-3 py-3.5 sm:items-center sm:px-5">
            <div className="min-w-0">
              <Dialog.Title className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
                <Library className="h-4 w-4 shrink-0 text-primary-soft" />
                本地人物形象素材库
              </Dialog.Title>
              <Dialog.Description className="mt-0.5 text-2xs text-zinc-400">
                {catalog
                  ? `${catalog.total_identities} 个身份 · ${catalog.total_assets} 张参考素材`
                  : '本地人物与多角度表情参考素材'}
              </Dialog.Description>
            </div>
            <Dialog.Close className="btn btn-sm btn-ghost shrink-0 p-1.5" aria-label="关闭素材库">
              <X className="h-4 w-4" />
            </Dialog.Close>
          </header>
          {loading ? (
            <div className="flex flex-1 items-center justify-center gap-2 text-sm text-zinc-400">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在建立素材索引…
            </div>
          ) : error ? (
            <div className="flex flex-1 items-center justify-center text-sm text-err" role="alert">
              {error}
            </div>
          ) : !catalog?.available ? (
            <div className="flex flex-1 items-center justify-center text-center text-sm text-zinc-400">
              素材目录不可用
            </div>
          ) : (
            <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)_minmax(240px,0.85fr)] lg:grid-cols-[minmax(0,1fr)_390px] lg:grid-rows-1">
              <section className="flex min-h-0 flex-col border-b border-border lg:border-b-0 lg:border-r">
                <div className="flex flex-col gap-2 border-b border-border p-3 sm:flex-row sm:items-center">
                  <div className="seg-track self-start">
                    <button
                      className={gender === 'all' ? 'seg-item-primary' : 'seg-item'}
                      onClick={() => setGender('all')}
                    >
                      全部 {catalog.identities.length}
                    </button>
                    {Array.from(new Set(catalog.identities.map((identity) => identity.gender))).map((value) => (
                      <button
                        key={value}
                        className={gender === value ? 'seg-item-primary' : 'seg-item'}
                        onClick={() => setGender(value)}
                      >
                        {value === 'female' ? '女性' : value === 'male' ? '男性' : value}{' '}
                        {catalog.identities.filter((identity) => identity.gender === value).length}
                      </button>
                    ))}
                  </div>
                  <label className="relative min-w-0 flex-1">
                    <span className="sr-only">搜索人物编号或年龄</span>
                    <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-zinc-600" />
                    <input
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      className="h-8 w-full pl-8"
                      placeholder="搜索编号或年龄"
                    />
                  </label>
                </div>
                {/* Explicit max-content rows prevent the fixed-height modal from shrinking every
                identity card until only a thin strip of each portrait remains visible. */}
                <div className="grid min-h-0 flex-1 auto-rows-max content-start grid-cols-2 gap-3 overflow-y-auto p-3 sm:grid-cols-3 md:grid-cols-4">
                  {identities.map((identity) => (
                    <button
                      key={identity.id}
                      onClick={() => chooseIdentity(identity)}
                      aria-pressed={selected?.id === identity.id}
                      className={
                        'group min-w-0 self-start overflow-hidden rounded-xl border text-left transition-all ' +
                        (selected?.id === identity.id
                          ? 'border-primary ring-2 ring-primary/25'
                          : 'border-border hover:border-border-md')
                      }
                    >
                      <div className="aspect-square overflow-hidden bg-surface-3">
                        <img
                          src={portraitAssetThumbnailUrl(identity.cover_asset_id)}
                          alt={identity.display_name}
                          className="h-full w-full object-cover transition-transform group-hover:scale-105"
                        />
                      </div>
                      <div className="p-2">
                        <div className="truncate text-xs font-semibold text-zinc-200">{identity.code}</div>
                        <div className="mt-0.5 text-2xs text-zinc-400">
                          {identity.age} 岁 · {identity.asset_count} 张
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              </section>
              <aside className="flex min-h-0 flex-col">
                {!selected ? (
                  <div className="flex flex-1 flex-col items-center justify-center text-center text-zinc-600">
                    <Users className="mb-3 h-9 w-9 opacity-30" />
                    <p className="text-xs">选择一位人物查看素材</p>
                  </div>
                ) : (
                  <>
                    <div className="border-b border-border p-3">
                      <div className="flex items-center gap-3">
                        <img
                          src={portraitAssetThumbnailUrl(selected.cover_asset_id)}
                          alt=""
                          className="h-14 w-14 shrink-0 rounded-xl border border-border object-cover"
                        />
                        <div className="min-w-0">
                          <div className="text-sm font-semibold text-zinc-100">{selected.display_name}</div>
                          <div className="mt-1 text-2xs text-zinc-400">
                            默认按正脸、左右侧脸和俯仰姿态分层选择；最多 8 张
                          </div>
                        </div>
                      </div>
                    </div>
                    <div className="grid min-h-0 flex-1 grid-cols-3 gap-2 overflow-y-auto p-3 sm:grid-cols-4">
                      {selected.assets.map((asset) => {
                        const checked = selectedAssetIds.has(asset.asset_id);
                        return (
                          <button
                            key={asset.asset_id}
                            onClick={() => toggleAsset(asset)}
                            aria-pressed={checked}
                            aria-label={`${checked ? '取消选择' : '选择'} ${asset.label}`}
                            className={
                              'relative aspect-square overflow-hidden rounded-lg border ' +
                              (checked
                                ? 'border-primary ring-2 ring-primary/25'
                                : 'border-border opacity-65 hover:opacity-100')
                            }
                          >
                            <img
                              src={portraitAssetThumbnailUrl(asset.asset_id)}
                              alt=""
                              className="h-full w-full object-cover"
                            />
                            {checked && (
                              <span className="absolute right-1 top-1 rounded-full bg-primary p-0.5 text-white">
                                <Check className="h-3 w-3" />
                              </span>
                            )}
                            <span className="absolute inset-x-0 bottom-0 truncate bg-black/70 px-1 py-0.5 text-[9px] text-zinc-300">
                              {asset.category === 'angle' ? '角度' : '表情'} · {asset.label.replace(/^\d+_/, '')}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                    <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-border p-3">
                      <span className="text-xs text-zinc-400">已选 {selectedAssetIds.size} / 8 张</span>
                      <button
                        className="btn btn-md btn-primary"
                        disabled={!selectedAssetIds.size}
                        onClick={importIdentity}
                      >
                        <User className="h-3.5 w-3.5" />
                        添加到人物素材
                      </button>
                    </footer>
                  </>
                )}
              </aside>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
};
