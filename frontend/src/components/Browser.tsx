import { useEffect, useState } from 'react';
import axios from 'axios';

export default function Browser() {
  const [data, setData] = useState<any>({ nodes: [] });
  const [query, setQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('All');
  const [sortKey, setSortKey] = useState<'label' | 'name'>('label');
  const [page, setPage] = useState(0);
  const pageSize = 50;

  useEffect(() => {
    const fetchData = async () => {
      try {
        const apiUrl = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8080';
        const response = await axios.get(`${apiUrl}/api/graph`);
        if (response.data && response.data.nodes) {
          setData(response.data);
        }
      } catch (error) {
        console.error("Error fetching data:", error);
      }
    };
    fetchData();
  }, []);

  const nodeTypes = Array.from(new Set<string>(data.nodes.map((node: any) => String(node.label)))).sort();
  const types: string[] = ['All', ...nodeTypes];
  const filteredNodes = data.nodes
    .filter((node: any) => typeFilter === 'All' || node.label === typeFilter)
    .filter((node: any) => JSON.stringify(node.properties || {}).toLowerCase().includes(query.toLowerCase()))
    .sort((a: any, b: any) => {
      const aValue = sortKey === 'name' ? (a.properties.name || a.properties.id || a.id) : a.label;
      const bValue = sortKey === 'name' ? (b.properties.name || b.properties.id || b.id) : b.label;
      return String(aValue).localeCompare(String(bValue));
    });
  const pagedNodes = filteredNodes.slice(page * pageSize, (page + 1) * pageSize);
  const totalPages = Math.max(1, Math.ceil(filteredNodes.length / pageSize));

  return (
    <div className="w-full h-full p-6 flex flex-col">
      <div className="mb-6 flex justify-between items-end gap-4">
        <div>
          <h2 className="text-2xl font-bold text-slate-100 tracking-wide">Data Browser</h2>
          <p className="text-slate-400 text-sm mt-1">Raw telemetry and ontological entities</p>
        </div>
        <div className="flex items-center gap-2">
          <input
            value={query}
            onChange={(event) => { setQuery(event.target.value); setPage(0); }}
            placeholder="Search properties"
            className="bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300"
          />
          <select value={typeFilter} onChange={(event) => { setTypeFilter(event.target.value); setPage(0); }} className="bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300">
            {types.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
          <select value={sortKey} onChange={(event) => setSortKey(event.target.value as 'label' | 'name')} className="bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300">
            <option value="label">Sort Type</option>
            <option value="name">Sort Name</option>
          </select>
        </div>
        <div className="bg-slate-800 border border-slate-700 px-4 py-2 rounded text-sm font-mono text-blue-400 shadow-inner">
          RECORDS: {filteredNodes.length}
        </div>
      </div>

      <div className="flex-1 overflow-auto bg-slate-800/50 border border-slate-700 rounded-lg shadow-xl">
        <table className="w-full text-left text-sm text-slate-300">
          <thead className="text-xs uppercase bg-slate-800/80 text-slate-400 sticky top-0 z-10 border-b border-slate-700">
            <tr>
              <th className="px-6 py-4 font-semibold tracking-wider">Type</th>
              <th className="px-6 py-4 font-semibold tracking-wider">Name / ID</th>
              <th className="px-6 py-4 font-semibold tracking-wider">Properties</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700/50">
            {pagedNodes.map((node: any) => (
              <tr key={node.id} className="hover:bg-slate-700/30 transition-colors">
                <td className="px-6 py-4 whitespace-nowrap">
                  <span className="bg-blue-500/20 text-blue-400 px-2 py-1 rounded text-xs font-mono">
                    {node.label}
                  </span>
                </td>
                <td className="px-6 py-4 font-medium text-slate-200">
                  {node.properties.name || node.id}
                </td>
                <td className="px-6 py-4 font-mono text-xs text-slate-400">
                  {Object.entries(node.properties).map(([k, v]) => (
                    k !== 'name' && (
                      <div key={k}>
                        <span className="text-slate-500">{k}:</span> {String(v)}
                      </div>
                    )
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex justify-end items-center gap-3 text-xs text-slate-400 font-mono">
        <button onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0} className="px-3 py-1 rounded border border-slate-700 disabled:opacity-40">Prev</button>
        <span>PAGE {page + 1} / {totalPages}</span>
        <button onClick={() => setPage(Math.min(totalPages - 1, page + 1))} disabled={page >= totalPages - 1} className="px-3 py-1 rounded border border-slate-700 disabled:opacity-40">Next</button>
      </div>
    </div>
  );
}
