import { useState, useRef, useEffect } from 'react';
import { Send, Bot, User } from 'lucide-react';
import axios from 'axios';

export default function AvaChat() {
  const [messages, setMessages] = useState<{role: 'user'|'bot', content: string}[]>([
    { role: 'bot', content: 'Ava initialized. Semantic ontology connected. Awaiting queries.' }
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [online, setOnline] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    const checkStatus = async () => {
      try {
        const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8080';
        const response = await axios.get(`${apiUrl}/api/health`);
        setOnline(Boolean(response.data.ai?.configured));
      } catch {
        setOnline(false);
      }
    };
    checkStatus();
  }, []);

  const handleSend = async () => {
    if (!input.trim() || loading) return;
    
    const userMsg = input.trim();
    setMessages(prev => [...prev, { role: 'user', content: userMsg }]);
    setInput('');
    setLoading(true);

    try {
      const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8080';
      const response = await axios.post(`${apiUrl}/api/chat`, { message: userMsg });
      setMessages(prev => [...prev, { role: 'bot', content: response.data.reply }]);
    } catch (error: any) {
      console.error("Chat error:", error);
      const detail = error.response?.data?.detail || 'Unable to reach cognitive engine.';
      setMessages(prev => [...prev, { role: 'bot', content: detail }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="w-full h-full flex flex-col items-center justify-center p-6">
      <div className="w-full max-w-4xl h-full flex flex-col bg-slate-800/80 border border-slate-700 rounded-xl shadow-2xl backdrop-blur-sm overflow-hidden">
        
        <div className="p-4 border-b border-slate-700 bg-slate-800 flex items-center gap-3 shadow-sm z-10">
          <div className="w-10 h-10 rounded-full bg-emerald-500/20 flex items-center justify-center border border-emerald-500/50 shadow-[0_0_10px_rgba(16,185,129,0.3)]">
            <Bot className="text-emerald-400" />
          </div>
          <div>
            <h2 className="font-bold text-slate-100 tracking-wide">Ava Cognitive Engine</h2>
            <p className={`text-xs font-mono tracking-widest flex items-center gap-1 ${online ? 'text-emerald-400' : 'text-amber-400'}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${online ? 'bg-emerald-500 animate-pulse' : 'bg-amber-500'}`}></span> {online ? 'ONLINE' : 'READ-ONLY LOCAL'}
            </p>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {messages.map((msg, i) => (
            <div key={i} className={`flex gap-4 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
              <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 shadow-lg ${msg.role === 'user' ? 'bg-blue-600' : 'bg-slate-800 border border-emerald-500/50'}`}>
                {msg.role === 'user' ? <User size={16} /> : <Bot size={16} className="text-emerald-400" />}
              </div>
              <div className={`max-w-[80%] rounded-2xl px-5 py-3 shadow-md whitespace-pre-wrap ${
                msg.role === 'user' 
                  ? 'bg-blue-600 text-white rounded-tr-sm' 
                  : 'bg-slate-700/80 text-slate-200 border border-slate-600 rounded-tl-sm'
              }`}>
                {msg.content}
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex gap-4">
              <div className="w-8 h-8 rounded-full bg-slate-800 border border-emerald-500/50 flex items-center justify-center shadow-lg">
                <Bot size={16} className="text-emerald-400" />
              </div>
              <div className="bg-slate-700/80 text-slate-400 border border-slate-600 rounded-2xl rounded-tl-sm px-5 py-3 flex items-center gap-2 shadow-md">
                <span className="w-2 h-2 bg-emerald-500 rounded-full animate-bounce"></span>
                <span className="w-2 h-2 bg-emerald-500 rounded-full animate-bounce" style={{animationDelay: '150ms'}}></span>
                <span className="w-2 h-2 bg-emerald-500 rounded-full animate-bounce" style={{animationDelay: '300ms'}}></span>
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        <div className="p-4 border-t border-slate-700 bg-slate-800 z-10">
          <div className="relative flex items-center">
            <input 
              type="text" 
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSend()}
              placeholder="Query the ontology..."
              className="w-full bg-slate-900/80 border border-slate-600 rounded-xl py-4 pl-6 pr-14 text-slate-200 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all font-mono text-sm placeholder-slate-500"
            />
            <button 
              onClick={handleSend}
              disabled={loading || !input.trim()}
              className="absolute right-2 p-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed shadow-md"
            >
              <Send size={18} />
            </button>
          </div>
        </div>

      </div>
    </div>
  );
}
