import { useEffect, useState } from 'react';
import axios from 'axios';

export default function Browser() {
  const [data, setData] = useState<any>({ nodes: [] });

  useEffect(() => {
    const fetchData = async () => {
      try {
        const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8080';
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

  return (
    <div className="w-full h-full p-6 flex flex-col">
      <div className="mb-6 flex justify-between items-end">
        <div>
          <h2 className="text-2xl font-bold text-slate-100 tracking-wide">Data Browser</h2>
          <p className="text-slate-400 text-sm mt-1">Raw telemetry and ontological entities</p>
        </div>
        <div className="bg-slate-800 border border-slate-700 px-4 py-2 rounded text-sm font-mono text-blue-400 shadow-inner">
          TOTAL RECORDS: {data.nodes.length}
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
            {data.nodes.map((node: any) => (
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
    </div>
  );
}
