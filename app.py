import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from supabase import create_client
from datetime import datetime, timedelta
from functools import wraps
import time
from threading import Lock
from collections import defaultdict
import secrets  # NOVA IMPORTAÇÃO

app = Flask(__name__)

# ==================== CONFIGURAÇÕES ====================
app.secret_key = os.environ.get("SECRET_KEY", "sua_chave_secreta_aqui_mude_isso_para_algo_seguro")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://pnpybnpbqwiteocpbcbb.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBucHlibnBicXdpdGVvY3BiY2JiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQwMDU0ODIsImV4cCI6MjA4OTU4MTQ4Mn0.LkBufgdceo1Qijj06g0dY2TyQmT7bOQSR9nPVpFUKm8")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== CACHE OTIMIZADO COM TTL ====================
class SimpleCache:
    def __init__(self, default_timeout=300):  # 5 minutos padrão
        self.cache = {}
        self.lock = Lock()
        self.default_timeout = default_timeout
    
    def get(self, key):
        with self.lock:
            if key in self.cache:
                data, timestamp = self.cache[key]
                if time.time() - timestamp < self.default_timeout:
                    return data
                del self.cache[key]
        return None
    
    def set(self, key, value, timeout=None):
        with self.lock:
            timeout = timeout or self.default_timeout
            self.cache[key] = (value, time.time())
    
    def clear(self, pattern=None):
        with self.lock:
            if pattern is None:
                self.cache.clear()
            else:
                keys_to_remove = [k for k in self.cache.keys() if pattern in k]
                for k in keys_to_remove:
                    del self.cache[k]
    
    def delete(self, key):
        with self.lock:
            if key in self.cache:
                del self.cache[key]

cache = SimpleCache(default_timeout=300)  # 5 minutos

# ==================== FUNÇÕES ANTI-DUPLICAÇÃO ====================
def generate_form_token():
    """Gera um token único para o formulário"""
    token = secrets.token_urlsafe(32)
    session['form_token'] = token
    session['form_token_time'] = datetime.now().isoformat()
    return token

def validate_form_token():
    """Valida o token do formulário e previne duplicação"""
    token = request.form.get('form_token')
    stored_token = session.get('form_token')
    token_time = session.get('form_token_time')
    
    # Verifica se token existe e é válido
    if not token or not stored_token or token != stored_token:
        return False
    
    # Verifica se o token não expirou (5 minutos)
    if token_time:
        try:
            token_dt = datetime.fromisoformat(token_time)
            if datetime.now() - token_dt > timedelta(minutes=5):
                return False
        except:
            pass
    
    # Remove o token após uso para evitar reuso
    session.pop('form_token', None)
    session.pop('form_token_time', None)
    return True

# ==================== DECORADORES ====================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash("Por favor, faça login para acessar esta página.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash("Por favor, faça login para acessar esta página.", "warning")
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash("Acesso negado. Você não tem permissão de administrador.", "error")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def cron_required(f):
    """Decorator para endpoints de cron job"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get("X-Cron-Secret") or request.args.get("key")
        expected_key = os.environ.get("CRON_SECRET", "sua_chave_secreta_cron_aqui")
        
        if api_key != expected_key:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated_function

def pode_editar_reserva(reserva_id, usuario_id, role):
    if role == 'admin':
        return True
    cache_key = f"reserva_owner_{reserva_id}"
    owner = cache.get(cache_key)
    if owner is None:
        reserva = supabase.table("reservas").select("usuario_id").eq("id", reserva_id).single().execute().data
        owner = reserva.get("usuario_id") if reserva else None
        cache.set(cache_key, owner, timeout=60)
    return owner == usuario_id

def pode_devolver_emprestimo(emprestimo_id, usuario_id, role):
    if role == 'admin':
        return True
    cache_key = f"emprestimo_owner_{emprestimo_id}"
    owner = cache.get(cache_key)
    if owner is None:
        emprestimo = supabase.table("emprestimos").select("usuario_id").eq("id", emprestimo_id).single().execute().data
        owner = emprestimo.get("usuario_id") if emprestimo else None
        cache.set(cache_key, owner, timeout=60)
    return owner == usuario_id

# ==================== CONFIGURAÇÕES ====================
CATEGORIAS = ["Esporte", "Educacional", "Outros"]
HORARIOS = ["1ª Aula", "2ª Aula", "3ª Aula", "4ª Aula", "5ª Aula"]
TURMAS_MANHA = ["8ºA", "8ºB", "8ºC", "8ºD", "9ºA", "9ºB", "9ºC", "9ºD"]
TURMAS_TARDE = ["6ºA", "6ºB", "6ºC", "6ºD", "6ºE", "7ºA", "7ºB", "7ºC"]

def get_turno_by_turma(turma):
    if turma in TURMAS_MANHA:
        return "Manhã"
    elif turma in TURMAS_TARDE:
        return "Tarde"
    return "Manhã"

# ==================== FUNÇÕES DE PROCESSAMENTO AUTOMÁTICO ====================
def processar_reservas_auto():
    """
    Processa automaticamente:
    1. Reservas que deveriam ser retiradas hoje ou antes
    2. Devoluções vencidas
    """
    try:
        hoje = datetime.now().strftime("%Y-%m-%d")
        processadas_reservas = 0
        adiadas = 0
        devolvidas_auto = 0
        erros = 0
        
        # ========== 1. PROCESSAR DEVOLUÇÕES VENCIDAS ==========
        emprestimos_vencidos = supabase.table("emprestimos")\
            .select("*")\
            .is_("data_devolucao_real", "null")\
            .not_.is_("data_devolucao_prevista", "null")\
            .lt("data_devolucao_prevista", hoje)\
            .execute().data
        
        for emprestimo in emprestimos_vencidos:
            try:
                supabase.table("emprestimos").update({
                    "data_devolucao_real": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "observacao": "Devolução automática por vencimento"
                }).eq("id", emprestimo["id"]).execute()
                devolvidas_auto += 1
                print(f"Devolução automática: Empréstimo {emprestimo['id']} vencido em {emprestimo['data_devolucao_prevista']}")
            except Exception as e:
                erros += 1
                print(f"Erro ao processar devolução automática {emprestimo.get('id')}: {str(e)}")
        
        # ========== 2. PROCESSAR RESERVAS (CORRIGIDO) ==========
        # Buscar reservas com data de retirada <= hoje (hoje ou passado)
        reservas = supabase.table("reservas")\
            .select("*")\
            .lte("data_retirada", hoje)\
            .execute().data
        
        for reserva in reservas:
            try:
                # Verificar disponibilidade para a data específica
                disponivel = get_disponibilidade_por_horario(
                    reserva["material_id"], 
                    reserva["data_retirada"], 
                    reserva["turno"], 
                    reserva["horario"]
                )
                
                if disponivel >= reserva["quantidade_reservada"]:
                    data_devolucao_prevista = (datetime.strptime(reserva["data_retirada"], "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
                    
                    supabase.table("emprestimos").insert({
                        "material_id": reserva["material_id"],
                        "aluno": reserva["aluno"],
                        "turma": reserva["turma"],
                        "turno": reserva["turno"],
                        "horario": reserva["horario"],
                        "quantidade_emprestada": reserva["quantidade_reservada"],
                        "usuario_id": reserva["usuario_id"],
                        "usuario_nome": reserva.get("usuario_nome", ""),
                        "data_emprestimo": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "data_emprestimo_data": reserva["data_retirada"],
                        "data_devolucao_prevista": data_devolucao_prevista
                    }).execute()
                    
                    supabase.table("reservas").delete().eq("id", reserva["id"]).execute()
                    processadas_reservas += 1
                    print(f"Reserva convertida: {reserva['aluno']} - Material {reserva['material_id']}")
                else:
                    # Adiar para amanhã
                    amanha = (datetime.strptime(reserva["data_retirada"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                    supabase.table("reservas").update({"data_retirada": amanha}).eq("id", reserva["id"]).execute()
                    adiadas += 1
                    print(f"Reserva adiada: {reserva['aluno']} para {amanha}")
                    
            except Exception as e:
                erros += 1
                print(f"Erro ao processar reserva {reserva.get('id')}: {str(e)}")
        
        if devolvidas_auto > 0 or processadas_reservas > 0 or adiadas > 0:
            cache.clear()
        
        # Log do resultado
        resultado = f"Processamento automático - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Devoluções: {devolvidas_auto}, Reservas convertidas: {processadas_reservas}, Adiadas: {adiadas}, Erros: {erros}"
        print(resultado)
        
        return {
            "success": True,
            "devolvidas_auto": devolvidas_auto,
            "processadas": processadas_reservas,
            "adiadas": adiadas,
            "erros": erros,
            "mensagem": f"Devoluções: {devolvidas_auto} | Reservas convertidas: {processadas_reservas} | Adiadas: {adiadas}"
        }
        
    except Exception as e:
        print(f"Erro crítico no processamento automático: {str(e)}")
        return {"success": False, "error": str(e)}
# ==================== FUNÇÕES OTIMIZADAS ====================
def get_todos_dados():
    """Busca TODOS os dados de uma vez e processa em memória com cache"""
    cache_key = "todos_dados"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    hoje = datetime.now().strftime("%Y-%m-%d")
    
    materiais = supabase.table("materiais").select("id,nome,categoria,quantidade_total,especificacoes,data_aquisicao,created_by").execute().data
    
    emprestimos = supabase.table("emprestimos")\
        .select("material_id, turno, horario, quantidade_emprestada, data_emprestimo_data")\
        .is_("data_devolucao_real", "null")\
        .eq("data_emprestimo_data", hoje)\
        .execute().data
    
    reservas = supabase.table("reservas")\
        .select("material_id, turno, horario, quantidade_reservada, data_retirada")\
        .eq("data_retirada", hoje)\
        .execute().data
    
    uso = {}
    
    for e in emprestimos:
        key = f"{e['material_id']}_{e['turno']}_{e['horario']}"
        uso[key] = uso.get(key, 0) + e.get("quantidade_emprestada", 1)
    
    for r in reservas:
        key = f"{r['material_id']}_{r['turno']}_{r['horario']}"
        uso[key] = uso.get(key, 0) + r.get("quantidade_reservada", 1)
    
    for material in materiais:
        total = material["quantidade_total"]
        horarios_manha = []
        horarios_tarde = []
        total_disponivel_manha = 0
        total_disponivel_tarde = 0
        
        for horario in HORARIOS:
            usado_manha = uso.get(f"{material['id']}_Manhã_{horario}", 0)
            disp_manha = total - usado_manha
            if disp_manha > 0:
                horarios_manha.append(horario)
                total_disponivel_manha += disp_manha
            
            usado_tarde = uso.get(f"{material['id']}_Tarde_{horario}", 0)
            disp_tarde = total - usado_tarde
            if disp_tarde > 0:
                horarios_tarde.append(horario)
                total_disponivel_tarde += disp_tarde
        
        material["horarios_manha_hoje"] = horarios_manha
        material["horarios_tarde_hoje"] = horarios_tarde
        material["total"] = total
        material["disponiveis_manha"] = total_disponivel_manha
        material["disponiveis_tarde"] = total_disponivel_tarde
        material["disponiveis"] = total_disponivel_manha + total_disponivel_tarde
    
    total_materiais = sum(m["quantidade_total"] for m in materiais)
    total_emprestados = supabase.table("emprestimos").select("id", count="exact").is_("data_devolucao_real", "null").execute().count or 0
    total_reservados = supabase.table("reservas").select("id", count="exact").execute().count or 0
    
    resultado = {
        'materiais': materiais,
        'total_materiais': total_materiais,
        'total_emprestados': total_emprestados,
        'total_reservados': total_reservados
    }
    
    cache.set(cache_key, resultado)
    return resultado

def get_disponibilidade_por_horario(material_id, data, turno, horario):
    """Obtém disponibilidade com cache"""
    cache_key = f"disp_{material_id}_{data}_{turno}_{horario}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    material = supabase.table("materiais").select("quantidade_total").eq("id", material_id).single().execute().data
    if not material:
        return 0
    
    total = material["quantidade_total"]
    
    emprestimos = supabase.table("emprestimos")\
        .select("quantidade_emprestada")\
        .eq("material_id", material_id)\
        .eq("data_emprestimo_data", data)\
        .eq("turno", turno)\
        .eq("horario", horario)\
        .is_("data_devolucao_real", "null")\
        .execute().data
    
    reservas = supabase.table("reservas")\
        .select("quantidade_reservada")\
        .eq("material_id", material_id)\
        .eq("data_retirada", data)\
        .eq("turno", turno)\
        .eq("horario", horario)\
        .execute().data
    
    total_emprestado = sum(e.get("quantidade_emprestada", 1) for e in emprestimos)
    total_reservado = sum(r.get("quantidade_reservada", 1) for r in reservas)
    disponivel = total - total_emprestado - total_reservado
    
    cache.set(cache_key, disponivel, timeout=60)
    return disponivel

# ==================== ROTAS DE AUTENTICAÇÃO ====================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip()
        senha = request.form["senha"].strip()
        
        usuario = supabase.table("usuarios")\
            .select("*")\
            .eq("email", email)\
            .execute().data
        
        if usuario and usuario[0]["senha"] == senha:
            session['usuario_id'] = usuario[0]["id"]
            session['usuario_nome'] = usuario[0]["nome"]
            session['usuario_email'] = usuario[0]["email"]
            session['role'] = usuario[0]["role"]
            flash(f"Bem-vindo, {usuario[0]['nome']}!", "success")
            return redirect(url_for("index"))
        else:
            flash("Email ou senha incorretos.", "error")
    
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    cache.clear()
    flash("Você saiu do sistema.", "success")
    return redirect(url_for("login"))

@app.route("/cadastrar_professor", methods=["GET", "POST"])
def cadastrar_professor():
    if request.method == "POST":
        email = request.form["email"].strip()
        senha = request.form["senha"].strip()
        nome = request.form["nome"].strip()
        confirmar_senha = request.form.get("confirmar_senha", "").strip()
        
        if not email or not senha or not nome:
            flash("Todos os campos são obrigatórios.", "error")
            return redirect(url_for("cadastrar_professor"))
        
        if senha != confirmar_senha:
            flash("As senhas não coincidem.", "error")
            return redirect(url_for("cadastrar_professor"))
        
        if len(senha) < 4:
            flash("A senha deve ter pelo menos 4 caracteres.", "error")
            return redirect(url_for("cadastrar_professor"))
        
        existente = supabase.table("usuarios").select("id").eq("email", email).execute().data
        if existente:
            flash("Este email já está cadastrado.", "error")
            return redirect(url_for("cadastrar_professor"))
        
        supabase.table("usuarios").insert({
            "email": email,
            "senha": senha,
            "nome": nome,
            "role": "professor",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }).execute()
        
        cache.clear()
        flash("Conta criada com sucesso! Faça login.", "success")
        return redirect(url_for("login"))
    
    return render_template("cadastrar_professor.html")

# ==================== ROTA PRINCIPAL COM PROCESSAMENTO AUTOMÁTICO ====================
@app.route("/")
@login_required
def index():
    try:
        hoje = datetime.now().strftime("%Y-%m-%d")
        cache_key_processado = f"processado_auto_{hoje}"
        
        if not cache.get(cache_key_processado):
            resultado = processar_reservas_auto()
            if resultado["success"] and (resultado["devolvidas_auto"] > 0 or resultado["processadas"] > 0 or resultado["adiadas"] > 0):
                flash(f"📅 Processamento automático: {resultado['mensagem']}", "info")
            cache.set(cache_key_processado, True, timeout=86400)
        
        dados = get_todos_dados()
        return render_template(
            "index.html",
            materiais=dados['materiais'],
            total_materiais=dados['total_materiais'],
            total_emprestados=dados['total_emprestados'],
            total_reservados=dados['total_reservados'],
            categorias=CATEGORIAS,
            usuario_nome=session.get('usuario_nome'),
            usuario_role=session.get('role')
        )
    except Exception as e:
        app.logger.error(f"Erro ao carregar dados: {str(e)}")
        return f"Erro ao carregar dados: {str(e)}", 500

# ==================== ENDPOINT PARA CRON JOB EXTERNO ====================
@app.route("/api/cron/processar-reservas", methods=["GET", "POST"])
@cron_required
def cron_processar_reservas():
    try:
        resultado = processar_reservas_auto()
        return jsonify(resultado), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================== ROTA ADMIN PARA PROCESSAR RESERVAS MANUALMENTE ====================
@app.route("/processar_reservas")
@admin_required
def processar_reservas_manual():
    try:
        resultado = processar_reservas_auto()
        if resultado["success"]:
            if resultado["devolvidas_auto"] > 0 or resultado["processadas"] > 0 or resultado["adiadas"] > 0:
                flash(f"✅ {resultado['mensagem']}", "success")
            else:
                flash("📌 Nenhuma reserva ou devolução pendente para processar.", "info")
        else:
            flash(f"❌ Erro ao processar: {resultado.get('error', 'Erro desconhecido')}", "error")
        
        cache.clear()
    except Exception as e:
        flash(f"Erro ao processar: {str(e)}", "error")
    
    return redirect(url_for("index"))

# ==================== CADASTRAR MATERIAL ====================
@app.route("/cadastrar", methods=["GET", "POST"])
@admin_required
def cadastrar():
    if request.method == "POST":
        nome = request.form["nome"].strip()
        categoria = request.form["categoria"]
        quantidade = int(request.form["quantidade"])
        especificacoes = request.form.get("especificacoes", "").strip()

        if quantidade < 1:
            flash("O material precisa ter pelo menos 1 unidade.", "error")
            return redirect(url_for("cadastrar"))

        existente = supabase.table("materiais")\
            .select("id")\
            .eq("nome", nome).eq("categoria", categoria)\
            .limit(1)\
            .execute().data

        if existente:
            flash(f"Material '{nome}' já está cadastrado nesta categoria.", "error")
            return redirect(url_for("index"))

        supabase.table("materiais").insert({
            "nome": nome,
            "categoria": categoria,
            "quantidade_total": quantidade,
            "especificacoes": especificacoes,
            "data_aquisicao": datetime.now().strftime("%Y-%m-%d"),
            "created_by": session['usuario_id']
        }).execute()
        
        cache.clear()
        flash(f"Material '{nome}' cadastrado com sucesso!", "success")
        return redirect(url_for("index"))

    return render_template("cadastrar.html", categorias=CATEGORIAS)

# ==================== EMPRESTAR/RESERVAR ====================
@app.route("/emprestar/<int:material_id>", methods=["GET", "POST"])
@login_required
def emprestar(material_id):
    dados = get_todos_dados()
    material = next((m for m in dados['materiais'] if m['id'] == material_id), None)
    
    if not material:
        flash("Material não encontrado.", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        aluno = session['usuario_nome']
        turma = request.form["turma"]
        horario_index = int(request.form.get("horario", 0))
        horario = HORARIOS[horario_index] if horario_index < len(HORARIOS) else HORARIOS[0]
        quantidade = int(request.form.get("quantidade", 1))
        data_retirada = request.form.get("data_retirada", datetime.now().strftime("%Y-%m-%d"))
        turno = get_turno_by_turma(turma)
        
        disponivel = get_disponibilidade_por_horario(material_id, data_retirada, turno, horario)
        
        if quantidade > disponivel:
            flash(f"Apenas {disponivel} unidades disponíveis para {turno.lower()} no dia {data_retirada} no horário {horario}.", "error")
            return redirect(url_for("emprestar", material_id=material_id))
        
        if quantidade <= 0:
            flash("Quantidade inválida.", "error")
            return redirect(url_for("emprestar", material_id=material_id))
        
        data_devolucao_prevista = (datetime.strptime(data_retirada, "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
        
        if data_retirada == datetime.now().strftime("%Y-%m-%d"):
            supabase.table("emprestimos").insert({
                "material_id": material_id,
                "aluno": aluno,
                "turma": turma,
                "turno": turno,
                "horario": horario,
                "quantidade_emprestada": quantidade,
                "usuario_id": session['usuario_id'],
                "usuario_nome": session['usuario_nome'],
                "data_emprestimo": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data_emprestimo_data": data_retirada,
                "data_devolucao_prevista": data_devolucao_prevista
            }).execute()
            flash(f"{quantidade}x '{material['nome']}' EMPRESTADO para {aluno} no horário {horario}!", "success")
        else:
            supabase.table("reservas").insert({
                "material_id": material_id,
                "aluno": aluno,
                "turma": turma,
                "turno": turno,
                "horario": horario,
                "quantidade_reservada": quantidade,
                "usuario_id": session['usuario_id'],
                "usuario_nome": session['usuario_nome'],
                "data_reserva": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data_retirada": data_retirada
            }).execute()
            flash(f"{quantidade}x '{material['nome']}' RESERVADO para {aluno} no horário {horario} para o dia {data_retirada}!", "warning")

        cache.clear()
        return redirect(url_for("index"))
    
    hoje = datetime.now().strftime("%Y-%m-%d")
    disponibilidades = {}
    for horario in HORARIOS:
        for turno in ["Manhã", "Tarde"]:
            disponibilidades[f"{turno}_{horario}"] = get_disponibilidade_por_horario(material_id, hoje, turno, horario)
    
    return render_template("emprestar.html", 
                         material=material, 
                         turmas_manha=TURMAS_MANHA,
                         turmas_tarde=TURMAS_TARDE,
                         horarios=HORARIOS,
                         disponibilidades=disponibilidades,
                         hoje=hoje)

# ==================== ATUALIZAR RESERVA ====================
@app.route("/atualizar_reserva/<int:reserva_id>", methods=["GET", "POST"])
@login_required
def atualizar_reserva(reserva_id):
    reserva = supabase.table("reservas").select("*, materiais(*)").eq("id", reserva_id).single().execute().data
    if not reserva:
        flash("Reserva não encontrada.", "error")
        return redirect(url_for("reservas"))
    
    if not pode_editar_reserva(reserva_id, session['usuario_id'], session['role']):
        flash("Você só pode editar suas próprias reservas.", "error")
        return redirect(url_for("reservas"))

    if request.method == "POST":
        nova_data = request.form.get("data_retirada")
        novo_horario_index = int(request.form.get("horario", 0))
        novo_horario = HORARIOS[novo_horario_index] if novo_horario_index < len(HORARIOS) else HORARIOS[0]
        
        disponivel = get_disponibilidade_por_horario(
            reserva["material_id"], nova_data, reserva["turno"], novo_horario
        )
        
        if reserva["quantidade_reservada"] > disponivel:
            flash(f"Apenas {disponivel} unidades disponíveis para {reserva['turno'].lower()} no dia {nova_data} no horário {novo_horario}.", "error")
            return redirect(url_for("atualizar_reserva", reserva_id=reserva_id))
        
        supabase.table("reservas").update({
            "data_retirada": nova_data,
            "horario": novo_horario
        }).eq("id", reserva_id).execute()
        
        cache.clear()
        flash(f"Reserva atualizada para o dia {nova_data} no horário {novo_horario}!", "success")
        return redirect(url_for("reservas"))
    
    return render_template("atualizar_reserva.html", reserva=reserva, horarios=HORARIOS, hoje=datetime.now().strftime("%Y-%m-%d"))

# ==================== CANCELAR RESERVA ====================
@app.route("/cancelar_reserva/<int:reserva_id>", methods=["POST"])
@login_required
def cancelar_reserva(reserva_id):
    if not pode_editar_reserva(reserva_id, session['usuario_id'], session['role']):
        flash("Você só pode cancelar suas próprias reservas.", "error")
        return redirect(url_for("reservas"))
    
    try:
        supabase.table("reservas").delete().eq("id", reserva_id).execute()
        cache.clear()
        flash("Reserva cancelada com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao cancelar reserva: {str(e)}", "error")
    
    return redirect(url_for("reservas"))

# ==================== DEVOLVER MATERIAL ====================
@app.route("/devolver/<int:emprestimo_id>", methods=["POST"])
@login_required
def devolver(emprestimo_id):
    if not pode_devolver_emprestimo(emprestimo_id, session['usuario_id'], session['role']):
        flash("Você só pode devolver materiais que pegou emprestado.", "error")
        return redirect(url_for("emprestimos_ativos"))
    
    try:
        supabase.table("emprestimos")\
            .update({
                "data_devolucao_real": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })\
            .eq("id", emprestimo_id)\
            .execute()
        
        cache.clear()
        flash("Material devolvido com sucesso!", "success")
    except Exception as e:
        flash(f"Erro na devolução: {str(e)}", "error")
    
    return redirect(url_for("emprestimos_ativos"))

# ==================== EXCLUIR MATERIAL ====================
@app.route("/excluir/<int:material_id>", methods=["POST"])
@admin_required
def excluir(material_id):
    supabase.table("materiais").delete().eq("id", material_id).execute()
    cache.clear()
    flash("Material excluído com sucesso!", "success")
    return redirect(url_for("index"))

# ==================== ATUALIZAR MATERIAL ====================
@app.route("/atualizar/<int:material_id>", methods=["GET", "POST"])
@admin_required
def atualizar(material_id):
    material = supabase.table("materiais").select("*").eq("id", material_id).single().execute().data
    if not material:
        return redirect(url_for("index"))

    if request.method == "POST":
        supabase.table("materiais").update({
            "nome": request.form["nome"],
            "categoria": request.form["categoria"],
            "quantidade_total": int(request.form["quantidade"]),
            "especificacoes": request.form.get("especificacoes", "")
        }).eq("id", material_id).execute()
        cache.clear()
        flash(f"Material '{material['nome']}' atualizado com sucesso!", "success")
        return redirect(url_for("index"))

    return render_template("atualizar.html", material=material, categorias=CATEGORIAS)

# ==================== BUSCAR / AUTOCOMPLETE ====================
@app.route("/buscar")
@login_required
def buscar():
    termo = request.args.get("q", "").strip()
    if not termo:
        return redirect(url_for("index"))

    dados = get_todos_dados()
    termo_lower = termo.lower()
    materiais = [m for m in dados['materiais'] if termo_lower in m['nome'].lower() or termo_lower in m['categoria'].lower()]

    return render_template("index.html",
                           materiais=materiais,
                           termo=termo,
                           total_materiais=len(materiais),
                           total_emprestados=0,
                           categorias=CATEGORIAS)

@app.route("/autocomplete")
@login_required
def autocomplete():
    termo = request.args.get("q", "")
    if not termo:
        return jsonify([])

    cache_key = f"autocomplete_{termo}"
    cached = cache.get(cache_key)
    if cached is not None:
        return jsonify(cached)
    
    dados = get_todos_dados()
    termo_lower = termo.lower()
    sugestoes = [m["nome"] for m in dados['materiais'] if termo_lower in m['nome'].lower()][:10]
    
    cache.set(cache_key, sugestoes, timeout=60)
    return jsonify(sugestoes)

# ==================== LISTAR EMPRÉSTIMOS ATIVOS ====================
@app.route("/emprestimos_ativos")
@login_required
def emprestimos_ativos():
    cache_key = "emprestimos_ativos_list"
    cached = cache.get(cache_key)
    
    if cached is not None:
        emprestimos, total_materiais, total_emprestados = cached
        return render_template("emprestimos_ativos.html",
                               emprestimos=emprestimos,
                               total_materiais=total_materiais,
                               total_emprestados=total_emprestados)
    
    dados = get_todos_dados()
    total_materiais = dados['total_materiais']
    
    emprestimos = supabase.table("emprestimos").select("*, materiais(*), usuarios!usuario_id(nome)")\
        .is_("data_devolucao_real", "null")\
        .order("data_emprestimo")\
        .execute().data
    
    for emp in emprestimos:
        if emp.get("usuarios"):
            emp["usuario_nome"] = emp["usuarios"]["nome"]
        hoje = datetime.now().strftime("%Y-%m-%d")
        if emp.get("data_devolucao_prevista") and emp["data_devolucao_prevista"] < hoje:
            emp["atrasado"] = True
        else:
            emp["atrasado"] = False
    
    total_emprestados = len(emprestimos)
    
    cache.set(cache_key, (emprestimos, total_materiais, total_emprestados), timeout=30)

    return render_template("emprestimos_ativos.html",
                           emprestimos=emprestimos,
                           total_materiais=total_materiais,
                           total_emprestados=total_emprestados)

# ==================== LISTAR HISTÓRICO DE EMPRÉSTIMOS ====================
@app.route("/historico_emprestimos")
@login_required
def historico_emprestimos():
    cache_key = "historico_emprestimos_list"
    cached = cache.get(cache_key)
    
    if cached is not None:
        emprestimos, total_emprestimos = cached
        return render_template("historico_emprestimos.html",
                               emprestimos=emprestimos,
                               total_emprestimos=total_emprestimos)
    
    emprestimos = supabase.table("emprestimos").select("*, materiais(*), usuarios!usuario_id(nome)")\
        .order("data_emprestimo", desc=True)\
        .limit(200)\
        .execute().data
    
    for emp in emprestimos:
        if emp.get("usuarios"):
            emp["usuario_nome"] = emp["usuarios"]["nome"]
    
    total_emprestimos = len(emprestimos)
    
    cache.set(cache_key, (emprestimos, total_emprestimos), timeout=60)
    
    return render_template("historico_emprestimos.html",
                           emprestimos=emprestimos,
                           total_emprestimos=total_emprestimos)

# ==================== LISTAR RESERVAS ====================
@app.route("/reservas")
@login_required
def reservas():
    cache_key = "reservas_list"
    cached = cache.get(cache_key)
    
    if cached is not None:
        reservas_list, total_reservas = cached
        return render_template("reservas.html", reservas=reservas_list, total_reservas=total_reservas)
    
    reservas_list = supabase.table("reservas").select("*, materiais(*), usuarios!usuario_id(nome)")\
        .order("data_retirada")\
        .execute().data
    
    for res in reservas_list:
        if res.get("usuarios"):
            res["usuario_nome"] = res["usuarios"]["nome"]
    
    total_reservas = len(reservas_list)
    
    cache.set(cache_key, (reservas_list, total_reservas), timeout=30)
    return render_template("reservas.html", reservas=reservas_list, total_reservas=total_reservas)

# ==================== ADMIN - USUÁRIOS ====================
@app.route("/admin/usuarios")
@admin_required
def listar_usuarios():
    cache_key = "usuarios_list"
    cached = cache.get(cache_key)
    
    if cached is not None:
        return render_template("admin_usuarios.html", usuarios=cached)
    
    usuarios = supabase.table("usuarios").select("*").order("created_at").execute().data
    cache.set(cache_key, usuarios, timeout=120)
    return render_template("admin_usuarios.html", usuarios=usuarios)

@app.route("/admin/usuarios/criar", methods=["GET", "POST"])
@admin_required
def criar_usuario():
    if request.method == "POST":
        email = request.form["email"].strip()
        senha = request.form["senha"].strip()
        nome = request.form["nome"].strip()
        role = request.form["role"]
        
        existente = supabase.table("usuarios").select("id").eq("email", email).execute().data
        if existente:
            flash("Este email já está cadastrado.", "error")
            return redirect(url_for("criar_usuario"))
        
        supabase.table("usuarios").insert({
            "email": email,
            "senha": senha,
            "nome": nome,
            "role": role,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }).execute()
        
        cache.clear()
        flash(f"Usuário {nome} criado com sucesso!", "success")
        return redirect(url_for("listar_usuarios"))
    
    return render_template("criar_usuario.html")

@app.route("/admin/usuarios/editar/<int:usuario_id>", methods=["GET", "POST"])
@admin_required
def editar_usuario(usuario_id):
    usuario = supabase.table("usuarios").select("*").eq("id", usuario_id).single().execute().data
    if not usuario:
        flash("Usuário não encontrado.", "error")
        return redirect(url_for("listar_usuarios"))
    
    if request.method == "POST":
        nome = request.form["nome"].strip()
        role = request.form["role"]
        senha = request.form.get("senha", "").strip()
        
        dados_update = {"nome": nome, "role": role}
        if senha:
            dados_update["senha"] = senha
        
        supabase.table("usuarios").update(dados_update).eq("id", usuario_id).execute()
        cache.clear()
        flash(f"Usuário {nome} atualizado com sucesso!", "success")
        return redirect(url_for("listar_usuarios"))
    
    return render_template("editar_usuario.html", usuario=usuario)

@app.route("/admin/usuarios/excluir/<int:usuario_id>", methods=["POST"])
@admin_required
def excluir_usuario(usuario_id):
    if usuario_id == session['usuario_id']:
        flash("Você não pode excluir seu próprio usuário.", "error")
        return redirect(url_for("listar_usuarios"))
    
    supabase.table("usuarios").delete().eq("id", usuario_id).execute()
    cache.clear()
    flash("Usuário excluído com sucesso!", "success")
    return redirect(url_for("listar_usuarios"))

@app.route("/admin/tornar_admin/<int:usuario_id>", methods=["POST"])
@admin_required
def tornar_admin(usuario_id):
    if usuario_id == session['usuario_id']:
        flash("Você já é administrador.", "warning")
        return redirect(url_for("listar_usuarios"))
    
    supabase.table("usuarios").update({"role": "admin"}).eq("id", usuario_id).execute()
    cache.clear()
    flash("Usuário promovido a administrador com sucesso!", "success")
    return redirect(url_for("listar_usuarios"))

@app.route("/admin/rebaixar_professor/<int:usuario_id>", methods=["POST"])
@admin_required
def rebaixar_professor(usuario_id):
    if usuario_id == session['usuario_id']:
        flash("Você não pode rebaixar a si mesmo.", "error")
        return redirect(url_for("listar_usuarios"))
    
    supabase.table("usuarios").update({"role": "professor"}).eq("id", usuario_id).execute()
    cache.clear()
    flash("Usuário rebaixado para professor.", "success")
    return redirect(url_for("listar_usuarios"))

# ==================== DASHBOARD ADMIN ====================
@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    cache_key = "admin_dashboard"
    cached = cache.get(cache_key)
    
    if cached is not None:
        return render_template("admin_dashboard.html", **cached)
    
    dados = get_todos_dados()
    total_materiais = dados['total_materiais']
    total_emprestimos = dados['total_emprestados']
    total_reservas = dados['total_reservados']
    
    usuarios = supabase.table("usuarios").select("id").execute().data
    total_usuarios = len(usuarios)
    
    hoje = datetime.now()
    emprestimos_por_dia = []
    for i in range(7):
        data = (hoje - timedelta(days=i)).strftime("%Y-%m-%d")
        count = supabase.table("emprestimos").select("id").eq("data_emprestimo_data", data).execute().data
        emprestimos_por_dia.append({"data": data, "total": len(count)})
    
    top_materiais = supabase.table("emprestimos").select("materiais(nome), quantidade_emprestada").execute().data
    
    materiais_count = {}
    for e in top_materiais:
        nome = e.get("materiais", {}).get("nome", "Desconhecido")
        qtd = e.get("quantidade_emprestada", 1)
        materiais_count[nome] = materiais_count.get(nome, 0) + qtd
    
    top_5 = sorted(materiais_count.items(), key=lambda x: x[1], reverse=True)[:5]
    
    ultimos_emprestimos = supabase.table("emprestimos").select(
        "*, materiais(nome), usuarios!usuario_id(nome), turma, horario, turno"
    ).order("data_emprestimo", desc=True).limit(10).execute().data
    
    for emp in ultimos_emprestimos:
        if emp.get("usuarios"):
            emp["usuario_nome"] = emp["usuarios"]["nome"]
        if not emp.get("turma"):
            emp["turma"] = "-"
        if not emp.get("horario"):
            emp["horario"] = "-"
        if not emp.get("turno"):
            emp["turno"] = "-"
    
    dashboard_data = {
        'total_materiais': total_materiais,
        'total_emprestimos': total_emprestimos,
        'total_reservas': total_reservas,
        'total_usuarios': total_usuarios,
        'emprestimos_por_dia': emprestimos_por_dia,
        'top_materiais': top_5,
        'ultimos_emprestimos': ultimos_emprestimos
    }
    
    cache.set(cache_key, dashboard_data, timeout=60)
    
    return render_template("admin_dashboard.html", **dashboard_data)

# ==================== OCORRÊNCIAS ====================
TIPOS_OCORRENCIA = [
    {"id": "disciplina", "nome": "Questões Disciplinares/Comportamentais", "icone": "⚠️"},
    {"id": "agressao", "nome": "Agressão Escolar", "icone": "👊"},
    {"id": "dano", "nome": "Danos ao Patrimônio", "icone": "💔"},
    {"id": "bullying", "nome": "Bullying e Cyberbullying", "icone": "😔"},
    {"id": "aparelhos", "nome": "Uso Indevido de Aparelhos", "icone": "📱"},
    {"id": "infracional", "nome": "Ato Infracional/Segurança", "icone": "🚨"},
    {"id": "pedagogica", "nome": "Ocorrências Pedagógicas", "icone": "📚"},
    {"id": "outros", "nome": "Outros", "icone": "📝"}
]

@app.route("/ocorrencias")
@login_required
def listar_ocorrencias():
    filtro_aluno = request.args.get("aluno", "").strip()
    filtro_turma = request.args.get("turma", "").strip()
    
    cache_key = f"ocorrencias_{session['usuario_id']}_{session['role']}_{filtro_aluno}_{filtro_turma}"
    cached = cache.get(cache_key)
    
    if cached is not None:
        ocorrencias, notificacoes_pendentes = cached
        return render_template("ocorrencias.html", 
                             ocorrencias=ocorrencias, 
                             filtro_aluno=filtro_aluno,
                             filtro_turma=filtro_turma,
                             turmas_manha=TURMAS_MANHA,
                             turmas_tarde=TURMAS_TARDE,
                             notificacoes_pendentes=notificacoes_pendentes,
                             tipos_map={t["id"]: t for t in TIPOS_OCORRENCIA})
    
    if session['role'] == 'admin':
        query = supabase.table("ocorrencias").select("*")
    else:
        query = supabase.table("ocorrencias").select("*").eq("usuario_id", session['usuario_id'])
    
    if filtro_aluno:
        query = query.ilike("nome_aluno", f"%{filtro_aluno}%")
    
    if filtro_turma:
        query = query.eq("turma", filtro_turma)
    
    ocorrencias = query.order("data_ocorrencia", desc=True).order("created_at", desc=True).execute().data
    
    notificacoes_pendentes = sum(1 for o in ocorrencias if o.get("notificar_pais"))
    
    cache.set(cache_key, (ocorrencias, notificacoes_pendentes), timeout=60)
    
    tipos_map = {t["id"]: t for t in TIPOS_OCORRENCIA}
    
    return render_template("ocorrencias.html", 
                         ocorrencias=ocorrencias, 
                         filtro_aluno=filtro_aluno,
                         filtro_turma=filtro_turma,
                         turmas_manha=TURMAS_MANHA,
                         turmas_tarde=TURMAS_TARDE,
                         notificacoes_pendentes=notificacoes_pendentes,
                         tipos_map=tipos_map)

@app.route("/ocorrencias/nova", methods=["GET", "POST"])
@login_required
def nova_ocorrencia():
    if request.method == "POST":
        # VALIDAÇÃO ANTI-DUPLICAÇÃO COM TOKEN
        if not validate_form_token():
            flash("Erro de validação. Por favor, tente novamente.", "error")
            return redirect(url_for("nova_ocorrencia"))
        
        nome_aluno = request.form["nome_aluno"].strip()
        turma = request.form["turma"]
        tipo_ocorrencia = request.form.get("tipo_ocorrencia", "outros")
        descricao_personalizada = request.form.get("descricao_personalizada", "").strip()
        observacao = request.form.get("observacao", "").strip()
        notificar_pais = request.form.get("notificar_pais") == "on"
        
        if not nome_aluno:
            flash("Nome do aluno é obrigatório.", "error")
            return redirect(url_for("nova_ocorrencia"))
        
        # VERIFICAÇÃO DE DUPLICATA NOS ÚLTIMOS 10 SEGUNDOS
        dez_segundos_atras = (datetime.now() - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
        
        duplicata = supabase.table("ocorrencias")\
            .select("id")\
            .eq("nome_aluno", nome_aluno)\
            .eq("turma", turma)\
            .eq("tipo_ocorrencia", tipo_ocorrencia)\
            .gte("created_at", dez_segundos_atras)\
            .eq("usuario_id", session['usuario_id'])\
            .execute().data
        
        if duplicata:
            flash("Essa ocorrência já foi registrada recentemente. Aguarde alguns segundos.", "warning")
            return redirect(url_for("listar_ocorrencias"))
        
        if tipo_ocorrencia == "outros" and descricao_personalizada:
            ocorrencia_text = descricao_personalizada
        else:
            tipo_nome = next((t["nome"] for t in TIPOS_OCORRENCIA if t["id"] == tipo_ocorrencia), "Outros")
            ocorrencia_text = tipo_nome
        
        supabase.table("ocorrencias").insert({
            "usuario_id": session['usuario_id'],
            "usuario_nome": session['usuario_nome'],
            "data_ocorrencia": datetime.now().strftime("%Y-%m-%d"),
            "nome_aluno": nome_aluno,
            "turma": turma,
            "ocorrencia": ocorrencia_text,
            "tipo_ocorrencia": tipo_ocorrencia,
            "descricao_personalizada": descricao_personalizada if tipo_ocorrencia == "outros" else None,
            "observacao": observacao,
            "notificar_pais": notificar_pais,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "visualizada": False
        }).execute()
        
        cache.clear()
        flash("Ocorrência registrada com sucesso!", "success")
        return redirect(url_for("listar_ocorrencias"))
    
    # Para GET, gera um novo token
    token = generate_form_token()
    return render_template("nova_ocorrencia.html", 
                         turmas_manha=TURMAS_MANHA, 
                         turmas_tarde=TURMAS_TARDE, 
                         datetime=datetime,
                         tipos_ocorrencia=TIPOS_OCORRENCIA,
                         form_token=token)

@app.route("/ocorrencias/editar/<int:ocorrencia_id>", methods=["GET", "POST"])
@login_required
def editar_ocorrencia(ocorrencia_id):
    ocorrencia = supabase.table("ocorrencias").select("*").eq("id", ocorrencia_id).single().execute().data
    
    if not ocorrencia:
        flash("Ocorrência não encontrada.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    if session['role'] != 'admin' and ocorrencia['usuario_id'] != session['usuario_id']:
        flash("Você só pode editar suas próprias ocorrências.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    if request.method == "POST":
        nome_aluno = request.form["nome_aluno"].strip()
        turma = request.form["turma"]
        tipo_ocorrencia = request.form.get("tipo_ocorrencia", "outros")
        descricao_personalizada = request.form.get("descricao_personalizada", "").strip()
        observacao = request.form.get("observacao", "").strip()
        notificar_pais = request.form.get("notificar_pais") == "on"
        
        if not nome_aluno:
            flash("Nome do aluno é obrigatório.", "error")
            return redirect(url_for("editar_ocorrencia", ocorrencia_id=ocorrencia_id))
        
        if tipo_ocorrencia == "outros" and descricao_personalizada:
            ocorrencia_text = descricao_personalizada
        else:
            tipo_nome = next((t["nome"] for t in TIPOS_OCORRENCIA if t["id"] == tipo_ocorrencia), "Outros")
            ocorrencia_text = tipo_nome
        
        supabase.table("ocorrencias").update({
            "nome_aluno": nome_aluno,
            "turma": turma,
            "ocorrencia": ocorrencia_text,
            "tipo_ocorrencia": tipo_ocorrencia,
            "descricao_personalizada": descricao_personalizada if tipo_ocorrencia == "outros" else None,
            "observacao": observacao,
            "notificar_pais": notificar_pais
        }).eq("id", ocorrencia_id).execute()
        
        cache.clear()
        flash("Ocorrência atualizada com sucesso!", "success")
        return redirect(url_for("listar_ocorrencias"))
    
    return render_template("editar_ocorrencia.html", 
                         ocorrencia=ocorrencia, 
                         turmas_manha=TURMAS_MANHA, 
                         turmas_tarde=TURMAS_TARDE,
                         tipos_ocorrencia=TIPOS_OCORRENCIA)

@app.route("/ocorrencias/visualizar/<int:ocorrencia_id>")
@login_required
def visualizar_ocorrencia(ocorrencia_id):
    ocorrencia = supabase.table("ocorrencias").select("*").eq("id", ocorrencia_id).single().execute().data
    
    if not ocorrencia:
        flash("Ocorrência não encontrada.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    if session['role'] != 'admin' and ocorrencia['usuario_id'] != session['usuario_id']:
        flash("Você não tem permissão para visualizar esta ocorrência.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    if session['role'] == 'admin' and not ocorrencia.get("visualizada"):
        supabase.table("ocorrencias").update({"visualizada": True}).eq("id", ocorrencia_id).execute()
        cache.clear()
    
    tipos_map = {t["id"]: t for t in TIPOS_OCORRENCIA}
    
    return render_template("visualizar_ocorrencia.html", 
                         ocorrencia=ocorrencia,
                         tipos_map=tipos_map)

@app.route("/ocorrencias/excluir/<int:ocorrencia_id>", methods=["POST"])
@login_required
def excluir_ocorrencia(ocorrencia_id):
    ocorrencia = supabase.table("ocorrencias").select("*").eq("id", ocorrencia_id).single().execute().data
    
    if not ocorrencia:
        flash("Ocorrência não encontrada.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    if session['role'] != 'admin' and ocorrencia['usuario_id'] != session['usuario_id']:
        flash("Você só pode excluir suas próprias ocorrências.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    supabase.table("ocorrencias").delete().eq("id", ocorrencia_id).execute()
    cache.clear()
    flash("Ocorrência excluída com sucesso!", "success")
    return redirect(url_for("listar_ocorrencias"))

@app.route("/ocorrencias/marcar_visualizada/<int:ocorrencia_id>", methods=["POST"])
@login_required
def marcar_visualizada(ocorrencia_id):
    if session['role'] != 'admin':
        return jsonify({"error": "Acesso negado"}), 403
    
    supabase.table("ocorrencias").update({"visualizada": True}).eq("id", ocorrencia_id).execute()
    cache.clear()
    return jsonify({"success": True})

@app.route("/ocorrencias/marcar_todas_visualizadas", methods=["POST"])
@login_required
def marcar_todas_visualizadas():
    if session['role'] != 'admin':
        return jsonify({"error": "Acesso negado"}), 403
    
    supabase.table("ocorrencias").update({"visualizada": True}).eq("visualizada", False).execute()
    cache.clear()
    return jsonify({"success": True})

# ==================== CONTEXT PROCESSOR GLOBAL ====================
@app.context_processor
def inject_global_variables():
    total_nao_visualizadas = 0
    if 'usuario_id' in session and session.get('role') == 'admin':
        cache_key = "total_nao_visualizadas"
        cached = cache.get(cache_key)
        if cached is not None:
            total_nao_visualizadas = cached
        else:
            try:
                nao_visualizadas = supabase.table("ocorrencias").select("id", count="exact").eq("visualizada", False).execute()
                total_nao_visualizadas = nao_visualizadas.count or 0
                cache.set(cache_key, total_nao_visualizadas, timeout=60)
            except:
                total_nao_visualizadas = 0
    return dict(total_nao_visualizadas=total_nao_visualizadas)

# ==================== PDF ====================
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from io import BytesIO
from flask import make_response
import urllib.parse
from collections import defaultdict
import os

def formatar_data(data_str):
    if not data_str:
        return "-"
    try:
        if '/' in data_str:
            return data_str
        partes = data_str.split('-')
        if len(partes) == 3:
            return f"{partes[2]}/{partes[1]}/{partes[0]}"
        return data_str
    except:
        return data_str

def criar_cabecalho_sem_texto():
    """Cria apenas a logo em formato retangular largo"""
    elements = []
    
    # Logo retangular: largura 16cm, altura 3cm
    logo_path = "static/logo_pdf.png"
    if os.path.exists(logo_path):
        try:
            logo = Image(logo_path, width=16*cm, height=3*cm)
            elements.append(logo)
            elements.append(Spacer(1, 0.5*cm))
        except Exception as e:
            print(f"⚠️ Erro ao carregar logo: {e}")
    
    return elements

@app.route("/ocorrencias/pdf/turma/<turma>")
@login_required
def pdf_por_turma_relatorio(turma):
    if session['role'] != 'admin':
        flash("Acesso negado. Apenas administradores podem gerar PDFs.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    ocorrencias = supabase.table("ocorrencias")\
        .select("*")\
        .eq("turma", turma)\
        .order("nome_aluno")\
        .order("data_ocorrencia", desc=True)\
        .execute().data
    
    if not ocorrencias:
        flash(f"Nenhuma ocorrência encontrada para a turma {turma}.", "warning")
        return redirect(url_for("listar_ocorrencias"))
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), 
                           rightMargin=1*cm, leftMargin=1*cm, 
                           topMargin=1.5*cm, bottomMargin=1.5*cm)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], 
                                  fontSize=16, alignment=1, 
                                  textColor=colors.HexColor('#00796b'),
                                  spaceAfter=8)
    normal_style = ParagraphStyle('Normal', parent=styles['Normal'], fontSize=9, leading=12)
    data_style = ParagraphStyle('DataStyle', parent=styles['Normal'], 
                                 fontSize=9, leading=12, alignment=0, 
                                 wordWrap='CJK', allowWidows=0, allowOrphans=0)
    
    elements = []
    
    elements.extend(criar_cabecalho_sem_texto())
    elements.append(Spacer(1, 15))
    
    elements.append(Paragraph(f"Relatório de Ocorrências - Turma {turma}", title_style))
    elements.append(Spacer(1, 20))
    
    # ========== ASSINATURA ACIMA DA TABELA ==========
    elements.append(Paragraph("Assinatura do Responsável", normal_style))
    elements.append(Spacer(1, 5))
    elements.append(Paragraph("_________________________________________", normal_style))
    elements.append(Spacer(1, 25))
    
    # Sequência: Data | Turma | Professor | Ocorrência | Observação
    data = []
    data.append([Paragraph("<b>Data</b>", normal_style),
                 Paragraph("<b>Turma</b>", normal_style),
                 Paragraph("<b>Professor</b>", normal_style),
                 Paragraph("<b>Ocorrência</b>", normal_style),
                 Paragraph("<b>Observação</b>", normal_style)])
    
    for o in ocorrencias:
        ocorrencia_text = o.get("ocorrencia", "")
        observacao_text = o.get("observacao", "") or "-"
        data_formatada = formatar_data(o.get("data_ocorrencia", "-"))
        
        data.append([
            Paragraph(data_formatada, data_style),
            Paragraph(o.get("turma", "-"), normal_style),
            Paragraph(o.get("usuario_nome", "-"), normal_style),
            Paragraph(ocorrencia_text, normal_style),
            Paragraph(observacao_text, normal_style)
        ])
    
    col_widths = [2.5*cm, 1.5*cm, 3*cm, 5*cm, 6*cm]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#00796b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('WORDWRAP', (0, 0), (-1, -1), 'CJK'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    
    elements.append(table)
    
    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=ocorrencias_{turma}_{datetime.now().strftime("%Y%m%d")}.pdf'
    return response


@app.route("/ocorrencias/pdf/aluno/<nome_aluno>")
@login_required
def pdf_por_aluno_relatorio(nome_aluno):
    if session['role'] != 'admin':
        flash("Acesso negado. Apenas administradores podem gerar PDFs.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    nome_aluno = urllib.parse.unquote(nome_aluno)
    
    ocorrencias = supabase.table("ocorrencias")\
        .select("*")\
        .ilike("nome_aluno", f"%{nome_aluno}%")\
        .order("data_ocorrencia", desc=True)\
        .execute().data
    
    if not ocorrencias:
        flash(f"Nenhuma ocorrência encontrada para o aluno {nome_aluno}.", "warning")
        return redirect(url_for("listar_ocorrencias"))
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), 
                           rightMargin=1*cm, leftMargin=1*cm, 
                           topMargin=1.5*cm, bottomMargin=1.5*cm)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], 
                                  fontSize=16, alignment=1, 
                                  textColor=colors.HexColor('#00796b'),
                                  spaceAfter=8)
    normal_style = ParagraphStyle('Normal', parent=styles['Normal'], fontSize=9, leading=12)
    data_style = ParagraphStyle('DataStyle', parent=styles['Normal'], 
                                 fontSize=9, leading=12, alignment=0, 
                                 wordWrap='CJK', allowWidows=0, allowOrphans=0)
    
    elements = []
    
    elements.extend(criar_cabecalho_sem_texto())
    elements.append(Spacer(1, 15))
    
    elements.append(Paragraph(f"Relatório de Ocorrências - Aluno: {nome_aluno}", title_style))
    elements.append(Spacer(1, 20))
    
    # ========== ASSINATURA ACIMA DA TABELA ==========
    elements.append(Paragraph("Assinatura do Responsável", normal_style))
    elements.append(Spacer(1, 5))
    elements.append(Paragraph("_________________________________________", normal_style))
    elements.append(Spacer(1, 25))
    
    # Sequência: Data | Turma | Professor | Ocorrência | Observação
    data = []
    data.append([Paragraph("<b>Data</b>", normal_style),
                 Paragraph("<b>Turma</b>", normal_style),
                 Paragraph("<b>Professor</b>", normal_style),
                 Paragraph("<b>Ocorrência</b>", normal_style),
                 Paragraph("<b>Observação</b>", normal_style)])
    
    for o in ocorrencias:
        ocorrencia_text = o.get("ocorrencia", "")
        observacao_text = o.get("observacao", "") or "-"
        data_formatada = formatar_data(o.get("data_ocorrencia", "-"))
        
        data.append([
            Paragraph(data_formatada, data_style),
            Paragraph(o.get("turma", "-"), normal_style),
            Paragraph(o.get("usuario_nome", "-"), normal_style),
            Paragraph(ocorrencia_text, normal_style),
            Paragraph(observacao_text, normal_style)
        ])
    
    col_widths = [2.5*cm, 1.5*cm, 3*cm, 5.5*cm, 6*cm]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#00796b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('WORDWRAP', (0, 0), (-1, -1), 'CJK'),
    ]))
    
    elements.append(table)
    
    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=ocorrencias_{nome_aluno}_{datetime.now().strftime("%Y%m%d")}.pdf'
    return response


@app.route("/ocorrencias/pdf/todas")
@login_required
def pdf_todas_ocorrencias_relatorio():
    if session['role'] != 'admin':
        flash("Acesso negado. Apenas administradores podem gerar PDFs.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    todas = supabase.table("ocorrencias")\
        .select("*")\
        .order("turma")\
        .order("nome_aluno")\
        .order("data_ocorrencia", desc=True)\
        .execute().data
    
    if not todas:
        flash("Nenhuma ocorrência encontrada.", "warning")
        return redirect(url_for("listar_ocorrencias"))
    
    por_turma = defaultdict(list)
    for o in todas:
        por_turma[o["turma"]].append(o)
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), 
                           rightMargin=1*cm, leftMargin=1*cm, 
                           topMargin=1.5*cm, bottomMargin=1.5*cm)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], 
                                  fontSize=16, alignment=1, 
                                  textColor=colors.HexColor('#00796b'),
                                  spaceAfter=8)
    turma_style = ParagraphStyle('TurmaTitle', parent=styles['Heading2'], 
                                  fontSize=12, textColor=colors.HexColor('#00796b'),
                                  spaceAfter=10, spaceBefore=15)
    normal_style = ParagraphStyle('Normal', parent=styles['Normal'], fontSize=9, leading=12)
    data_style = ParagraphStyle('DataStyle', parent=styles['Normal'], 
                                 fontSize=9, leading=12, alignment=0, 
                                 wordWrap='CJK', allowWidows=0, allowOrphans=0)
    
    elements = []
    elements.extend(criar_cabecalho_sem_texto())
    elements.append(Spacer(1, 15))
    
    elements.append(Paragraph("Relatório Geral de Ocorrências", title_style))
    elements.append(Spacer(1, 20))
    
    for turma in sorted(por_turma.keys()):
        elements.append(Paragraph(f"Turma {turma}", turma_style))
        
        # ========== ASSINATURA ACIMA DA TABELA (apenas na primeira turma ou antes de cada?) 
        # Vamos colocar antes da primeira turma apenas
        if loop.first:
            elements.append(Paragraph("Assinatura do Responsável", normal_style))
            elements.append(Spacer(1, 5))
            elements.append(Paragraph("_________________________________________", normal_style))
            elements.append(Spacer(1, 25))
        
        # Sequência: Data | Aluno | Professor | Ocorrência | Observação
        data = []
        data.append([Paragraph("<b>Data</b>", normal_style),
                     Paragraph("<b>Aluno</b>", normal_style),
                     Paragraph("<b>Professor</b>", normal_style),
                     Paragraph("<b>Ocorrência</b>", normal_style),
                     Paragraph("<b>Observação</b>", normal_style)])
        
        for o in por_turma[turma]:
            ocorrencia_text = o.get("ocorrencia", "")
            observacao_text = o.get("observacao", "") or "-"
            data_formatada = formatar_data(o.get("data_ocorrencia", "-"))
            
            data.append([
                Paragraph(data_formatada, data_style),
                Paragraph(o.get("nome_aluno", "-"), normal_style),
                Paragraph(o.get("usuario_nome", "-"), normal_style),
                Paragraph(ocorrencia_text, normal_style),
                Paragraph(observacao_text, normal_style)
            ])
        
        col_widths = [2.2*cm, 2.5*cm, 2.8*cm, 5.5*cm, 6*cm]
        table = Table(data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#00796b')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('WORDWRAP', (0, 0), (-1, -1), 'CJK'),
        ]))
        
        elements.append(table)
        elements.append(Spacer(1, 15))
    
    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=ocorrencias_todas_{datetime.now().strftime("%Y%m%d")}.pdf'
    return response

if __name__ == "__main__":
    app.run(debug=True)