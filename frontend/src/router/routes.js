export const pages = ['overview', 'inputs', 'trains', 'run', 'results', 'review', 'delivery'];

export const pageLabel = (spec, page) => spec.pages[pages.indexOf(page)] ?? page;

export function route() {
  const [page, id, offset] = location.hash.slice(1).split('/');
  return {page:pages.includes(page) ? page : 'overview', id:Number(id) || null, offset:Math.max(0, Number(offset) || 0)};
}
