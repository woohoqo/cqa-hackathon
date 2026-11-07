/** Validate a transient import request without rewriting a signed URL. */
export function organizerGroupFromUrl(apiUrl: string): string | undefined {
  try {
    const group = new URL(apiUrl.trim()).searchParams.get('group_id');
    return group && /^[A-Za-z0-9_-]{1,100}$/.test(group) ? group : undefined;
  } catch { return undefined; }
}

export function organizerImportRequest(groupId: string, apiUrl = ''): { group_id: string; api_url?: string } {
  const group_id = groupId.trim();
  if (!/^[A-Za-z0-9_-]{1,100}$/.test(group_id)) {
    throw new Error('В поле «Группа набора» укажите только ID, например DEV. Ссылку вставьте в отдельное поле выше.');
  }
  const api_url = apiUrl.trim();
  if (!api_url) return { group_id };
  try {
    const parsed = new URL(api_url);
    if (api_url.length > 8192 || !/^https:\/\//i.test(api_url) || /[\s\u0000-\u001f\u007f\\]/u.test(api_url) || parsed.protocol !== 'https:'
        || !parsed.hostname || parsed.username || parsed.password || parsed.hash || parsed.port === '0') {
      throw new Error();
    }
  } catch {
    throw new Error('Вставьте полную HTTPS-ссылку API организаторов без пробелов, логина и пароля в адресе.');
  }
  return { group_id, api_url };
}
