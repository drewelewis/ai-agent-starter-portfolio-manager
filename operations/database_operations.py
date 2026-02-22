"""
Database Operations
Handles all database interactions for book authoring projects.
"""

import asyncio
import json
from typing import Dict, Any, List, Optional
from datetime import datetime
from uuid import UUID
import asyncpg
from functools import wraps
import time
from config.settings import settings
from utils.logger import logger
from models import (
    BookCreate, Book, BookUpdate,
    ChapterCreate, Chapter, ChapterUpdate,
    ResearchItemCreate, ResearchItem, ResearchItemUpdate,
    OutlineCreate, Outline,
    FeedbackCreate, Feedback,
    QualityCheckCreate, QualityCheck,
    AuditLogCreate, AuditLog,
    AgentExecutionCreate, AgentExecution, AgentExecutionUpdate,
    CostEntryCreate, CostEntry
)


def retry_on_db_error(max_retries: int = 3, delay: float = 0.5):
    """Decorator to retry database operations on transient errors"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (asyncpg.ConnectionDoesNotExistError, 
                        asyncpg.InterfaceError,
                        asyncpg.TooManyConnectionsError) as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        wait_time = delay * (2 ** attempt)  # Exponential backoff
                        logger.warning(f"Database error on attempt {attempt + 1}/{max_retries}, retrying in {wait_time}s: {e}")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"Database operation failed after {max_retries} attempts: {e}")
                        raise
                except Exception as e:
                    # Non-transient errors should fail immediately
                    logger.error(f"Non-retryable database error: {e}")
                    raise
            raise last_error
        return wrapper
    return decorator


class DatabaseOperations:
    """Handles PostgreSQL database operations with shared connection pool."""
    
    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None
        self.agent_name = "database_operations"
        self._pool_health_check_interval = 60  # seconds
    
    @property
    def pool(self) -> Optional[asyncpg.Pool]:
        """Get the shared database connection pool."""
        return self._pool
    
    async def initialize(self):
        """Initialize the shared database connection pool."""
        if self._pool is not None:
            logger.info("Database pool already initialized, skipping")
            return
            
        print(f"DEBUG: Connecting to database:")
        print(f"  host={settings.postgres_host}")
        print(f"  port={settings.postgres_port}")
        print(f"  user={settings.postgres_user}")
        print(f"  database={settings.postgres_db}")
        print(f"  password={'*' * len(settings.postgres_password)}")
        print(f"  ssl='disable'")
        self._pool = await asyncpg.create_pool(
            host=settings.postgres_host,
            port=settings.postgres_port,
            user=settings.postgres_user,
            password=settings.postgres_password,
            database=settings.postgres_db,
            min_size=5,
            max_size=20,  # Shared by all agents
            max_queries=50000,  # Recycle connections after queries
            max_inactive_connection_lifetime=3600,  # 1 hour timeout
            command_timeout=120,  # 2 minute query timeout (AI operations can be slow)
            timeout=30,  # 30 second connection acquisition timeout
            ssl='disable',
            # Health check query
            init=self._init_connection
        )
        
        # Verify pool was created successfully
        if self._pool is None:
            raise RuntimeError("Failed to create database connection pool")
            
        # Test a connection immediately
        try:
            async with self._pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1")
                if result != 1:
                    raise RuntimeError("Database connection test failed")
        except Exception as e:
            # Clean up failed pool
            if self._pool:
                await self._pool.close()
                self._pool = None
            raise RuntimeError(f"Database connection validation failed: {e}")
            
        logger.info(f"{self.agent_name}: Shared database connection pool initialized and verified")
    
    async def _init_connection(self, conn):
        """Initialize connection with settings and health check"""
        # Set statement timeout for safety (2 minutes to match command_timeout)
        await conn.execute('SET statement_timeout = 120000')  # 120 seconds
        # Set application name for monitoring
        await conn.execute(f"SET application_name = 'book_authoring_{self.agent_name}'")
    
    async def health_check(self) -> bool:
        """Check if database connection is healthy"""
        try:
            if not self.pool:
                return False
            async with self.pool.acquire() as conn:
                await conn.fetchval('SELECT 1')
            return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False
    
    @retry_on_db_error(max_retries=3)
    async def execute_query(self, query: str, params: tuple = None) -> List[Dict[str, Any]]:
        """Execute a SELECT query and return results as list of dictionaries.
        Automatically converts %s placeholders to $1, $2, etc. for asyncpg."""
        # Convert %s to $1, $2, $3 for asyncpg (in order)
        if '%s' in query and params:
            converted_query = query
            for i in range(1, len(params) + 1):
                converted_query = converted_query.replace('%s', f'${i}', 1)
            query = converted_query
        
        async with self.pool.acquire() as conn:
            if params:
                rows = await conn.fetch(query, *params)
            else:
                rows = await conn.fetch(query)
            return [dict(row) for row in rows] if rows else []
    
    @retry_on_db_error(max_retries=3)
    async def execute_update(self, query: str, params: tuple = None) -> None:
        """Execute an INSERT, UPDATE, or DELETE query.
        Automatically converts %s placeholders to $1, $2, etc. for asyncpg."""
        # Convert %s to $1, $2, $3 for asyncpg (in order)
        if '%s' in query and params:
            converted_query = query
            for i in range(1, len(params) + 1):
                converted_query = converted_query.replace('%s', f'${i}', 1)
            query = converted_query
        
        async with self.pool.acquire() as conn:
            if params:
                await conn.execute(query, *params)
            else:
                await conn.execute(query)
    
    @retry_on_db_error(max_retries=3)
    async def create_project(self, book: BookCreate, allow_concurrent: bool = False) -> UUID:
        """Create a new book project.
        
        Args:
            book: BookCreate model with validated data
            allow_concurrent: If False (default), blocks creation if another project is active
            
        Returns:
            UUID of the created book
            
        Raises:
            ValueError: If another project is active and allow_concurrent=False
        """
        async with self.pool.acquire() as conn:
            # Sequential processing: Check for active projects
            if not allow_concurrent:
                active_projects = await conn.fetch(
                    """
                    SELECT book_id, title, status 
                    FROM books 
                    WHERE status NOT IN ('completed', 'cancelled', 'published')
                    ORDER BY created_at DESC
                    """
                )
                
                if active_projects:
                    active = active_projects[0]
                    raise ValueError(
                        f"Cannot create new project. Another book is currently being worked on: "
                        f"'{active['title']}' (status: {active['status']}). "
                        f"Please wait for it to complete or cancel it first."
                    )
            
            row = await conn.fetchrow(
                """
                INSERT INTO books (title, description, genre, target_audience, status, created_by, user_id, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING book_id
                """,
                book.title,
                book.description,
                book.genre,
                book.target_audience,
                book.status.value,  # Get enum value
                book.created_by,
                book.user_id,  # Include user_id
                json.dumps(book.metadata) if book.metadata else '{}'
            )
            return row['book_id']
    
    @retry_on_db_error(max_retries=3)
    async def get_project(self, book_id: UUID) -> Optional[Dict[str, Any]]:
        """Get project details by ID."""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                "SELECT * FROM books WHERE book_id = $1",
                book_id
            )
            return dict(result) if result else None
    
    async def find_active_projects(self) -> List[Dict[str, Any]]:
        """Find all projects that are not completed."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM books 
                WHERE status NOT IN ('completed', 'cancelled', 'published')
                ORDER BY created_at DESC
                """
            )
            return [dict(row) for row in rows]
    
    async def get_all_active_projects(self) -> List[Dict[str, Any]]:
        """Get all active projects (not completed/cancelled/published).
        
        This is the method used by SupervisorAgent for monitoring.
        Returns list ordered by creation date (oldest first for sequential processing).
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT book_id, title, status, created_at, updated_at
                FROM books 
                WHERE status NOT IN ('completed', 'cancelled', 'published')
                ORDER BY created_at ASC
                """
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_project_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Find a project by title."""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                "SELECT * FROM books WHERE title = $1 ORDER BY created_at DESC LIMIT 1",
                title
            )
            return dict(result) if result else None
    
    async def update_project_status(self, book_id: UUID, status: str) -> bool:
        """Update project status and recalculate completion percentage."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE books SET status = $1, updated_at = NOW() WHERE book_id = $2",
                status, book_id
            )
        
        # Update completion percentage after status change
        await self.update_project_completion(book_id)
        return True

    async def calculate_project_completion(self, book_id: UUID) -> int:
        """Calculate project completion percentage based on workflow progress.
        
        Returns:
            Integer from 0-100 representing completion percentage
        """
        async with self.pool.acquire() as conn:
            # Get project and chapter status information
            result = await conn.fetchrow("""
                SELECT 
                    b.status as project_status,
                    b.approved_by as project_approved_by,
                    o.status as outline_status,
                    o.approved_by as outline_approved_by,
                    COUNT(c.chapter_id) as total_chapters,
                    COUNT(CASE WHEN c.status = 'todo' THEN 1 END) as todo_chapters,
                    COUNT(CASE WHEN c.status = 'doing' THEN 1 END) as doing_chapters,
                    COUNT(CASE WHEN c.status = 'done' THEN 1 END) as done_chapters,
                    COUNT(CASE WHEN c.status = 'done' AND c.approved_by IS NOT NULL THEN 1 END) as approved_chapters
                FROM books b
                LEFT JOIN book_outlines o ON o.book_id = b.book_id AND o.status != 'cancelled'
                LEFT JOIN book_chapters c ON c.book_id = b.book_id AND c.status != 'cancelled'
                WHERE b.book_id = $1
                GROUP BY b.status, b.approved_by, o.status, o.approved_by
            """, book_id)
            
            if not result:
                return 0
                
            project_status = result['project_status']
            outline_status = result['outline_status']
            total_chapters = result['total_chapters'] or 0
            
            # Base percentage by project workflow stage
            base_percentage = {
                'todo': 5,      # Project not started
                'doing': 30,    # Project in progress
                'done': 90,     # Project complete (may need final approval)
                'paused': 0,    # Use chapter progress for paused projects
                'cancelled': 0
            }.get(project_status, 0)
            
            # For cancelled projects, return 0
            if project_status == 'cancelled':
                return 0
                
            # For fully approved projects, return 100%
            project_approved_by = result['project_approved_by']
            if project_status == 'done' and project_approved_by:
                return 100
                
            # If no chapters yet, use base percentage
            if total_chapters == 0:
                return base_percentage
                
            # Calculate chapter-based progress for new status system
            chapter_weights = {
                'todo': 1,      # Chapter not started
                'doing': 5,     # Chapter in progress  
                'done': 10      # Chapter complete
            }
            
            # Count chapters by new status
            todo_chapters = result.get('todo_chapters', 0) or 0
            doing_chapters = result.get('doing_chapters', 0) or 0  
            done_chapters = result.get('done_chapters', 0) or 0
            
            total_chapter_progress = (
                todo_chapters * chapter_weights['todo'] +
                doing_chapters * chapter_weights['doing'] +
                done_chapters * chapter_weights['done']
            )
            
            max_possible_progress = total_chapters * chapter_weights['done']
            chapter_percentage = int((total_chapter_progress / max_possible_progress) * 70) if max_possible_progress > 0 else 0
            
            # Combine base workflow percentage with chapter progress
            # Base workflow: 30% weight, Chapter progress: 70% weight
            final_percentage = min(100, int(base_percentage * 0.3 + chapter_percentage))
            
            # Approval gate: if outline not approved yet, cap at 15%
            outline_approved_by = result['outline_approved_by']
            if outline_status == 'done' and not outline_approved_by and final_percentage > 15:
                final_percentage = 15
                
            return final_percentage

    async def update_project_completion(self, book_id: UUID) -> int:
        """Calculate and update project completion percentage.
        
        Returns:
            Updated completion percentage
        """
        completion_percentage = await self.calculate_project_completion(book_id)
        
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE books SET percent_complete = $1, updated_at = NOW() WHERE book_id = $2",
                completion_percentage, book_id
            )
            
        return completion_percentage

    async def calculate_project_status_rollup(self, book_id: UUID) -> str:
        """Calculate what project status should be based on child entity states.
        
        Rollup Rules:
        1. todo: Project just created, no outline exists yet
        2. doing: Outline exists OR chapters in progress OR outline needs approval
        3. done: Outline approved AND all chapters are done (ready for publishing)
        
        Returns:
            Calculated project status ('todo', 'doing', 'done')
        """
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow("""
                SELECT 
                    -- Outline info
                    o.status as outline_status,
                    o.approved_by as outline_approved_by,
                    -- Chapter counts
                    COUNT(c.chapter_id) as total_chapters,
                    COUNT(CASE WHEN c.status = 'todo' THEN 1 END) as todo_chapters,
                    COUNT(CASE WHEN c.status = 'doing' THEN 1 END) as doing_chapters,
                    COUNT(CASE WHEN c.status = 'done' THEN 1 END) as done_chapters
                FROM books b
                LEFT JOIN book_outlines o ON o.book_id = b.book_id AND o.status != 'cancelled'
                LEFT JOIN book_chapters c ON c.book_id = b.book_id AND c.status != 'cancelled'
                WHERE b.book_id = $1
                GROUP BY o.status, o.approved_by
            """, book_id)
            
            if not result:
                return 'todo'  # Project doesn't exist or has no data
            
            outline_status = result['outline_status']
            outline_approved = result['outline_approved_by'] is not None
            total_chapters = result['total_chapters'] or 0
            doing_chapters = result['doing_chapters'] or 0
            done_chapters = result['done_chapters'] or 0
            
            # Rule 1: No outline yet = project is todo
            if not outline_status:
                return 'todo'
            
            # Rule 2: Outline exists but not approved = project is doing
            if outline_status == 'done' and not outline_approved:
                return 'doing'
            
            # Rule 3: Outline not complete yet = project is doing  
            if outline_status in ['todo', 'doing']:
                return 'doing'
                
            # Rule 4: Outline approved but chapters not all done = project is doing
            if outline_approved and total_chapters > 0 and done_chapters < total_chapters:
                return 'doing'
                
            # Rule 5: Outline approved and no chapters yet = project is doing (research phase)
            if outline_approved and total_chapters == 0:
                return 'doing'
                
            # Rule 6: Any chapters still in progress = project is doing
            if doing_chapters > 0:
                return 'doing'
                
            # Rule 7: Outline approved AND all chapters done = project is done
            if outline_approved and total_chapters > 0 and done_chapters == total_chapters:
                return 'done'
                
            # Default fallback
            return 'doing'

    async def update_project_status_from_rollup(self, book_id: UUID) -> str:
        """Update project status based on child entity rollup rules.
        
        Returns:
            New project status after rollup
        """
        calculated_status = await self.calculate_project_status_rollup(book_id)
        
        # Get current status to see if it changed
        async with self.pool.acquire() as conn:
            current = await conn.fetchrow(
                "SELECT status FROM books WHERE book_id = $1", book_id
            )
            
            current_status = current['status'] if current else None
            
            # Don't override manual states (paused, cancelled)
            if current_status in ['paused', 'cancelled']:
                return current_status
            
            # Update if status changed
            if current_status != calculated_status:
                await conn.execute(
                    "UPDATE books SET status = $1, updated_at = NOW() WHERE book_id = $2",
                    calculated_status, book_id
                )
                logger.info(f"Project {book_id} status rolled up: {current_status} → {calculated_status}")
                
                # Update completion percentage after status change
                await self.update_project_completion(book_id)
                
        return calculated_status

    async def approve_entity(
        self,
        entity_type: str,  # 'book', 'outline', 'chapter'
        entity_id: UUID,
        approved_by: str,
        approved: bool = True
    ) -> bool:
        """Universal approval function for any entity.
        
        Args:
            entity_type: 'book', 'outline', or 'chapter'
            entity_id: UUID of the entity (book_id, outline_id, or chapter_id)
            approved_by: Name of approver
            approved: True to approve, False to reject
            
        Returns:
            True if successful
        """
        table_map = {
            'book': 'books',
            'outline': 'book_outlines', 
            'chapter': 'book_chapters'
        }
        
        id_column_map = {
            'book': 'book_id',
            'outline': 'outline_id',
            'chapter': 'chapter_id'
        }
        
        if entity_type not in table_map:
            raise ValueError(f"Invalid entity_type: {entity_type}")
            
        table_name = table_map[entity_type]
        id_column = id_column_map[entity_type]
        
        async with self.pool.acquire() as conn:
            if approved:
                # Set approval
                await conn.execute(
                    f"""
                    UPDATE {table_name}
                    SET approved_by = $1, approved_date = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE {id_column} = $2 AND status = 'done'
                    """,
                    approved_by,
                    entity_id
                )
            else:
                # Rejection - cancel the entity
                await conn.execute(
                    f"""
                    UPDATE {table_name}
                    SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP  
                    WHERE {id_column} = $1
                    """,
                    entity_id
                )
                
        logger.info(f"{entity_type.title()} {entity_id} {'approved' if approved else 'rejected'} by {approved_by}")
        return True

    async def get_entities_awaiting_approval(self, book_id: UUID) -> List[Dict]:
        """Get all entities that are done but awaiting approval.
        
        Returns:
            List of entities with status='done' but approved_by=NULL
        """
        async with self.pool.acquire() as conn:
            # Get books awaiting approval
            books = await conn.fetch(
                """
                SELECT 'book' as entity_type, book_id as entity_id, book_id, title as name, 
                       status, approved_by, approved_date, updated_at
                FROM books
                WHERE book_id = $1 AND status = 'done' AND approved_by IS NULL
                """,
                book_id
            )
            
            # Get outlines awaiting approval
            outlines = await conn.fetch(
                """
                SELECT 'outline' as entity_type, outline_id as entity_id, book_id, 
                       CONCAT('Outline for ', (SELECT title FROM books WHERE books.book_id = book_outlines.book_id)) as name,
                       status, approved_by, approved_date, updated_at
                FROM book_outlines
                WHERE book_id = $1 AND status = 'done' AND approved_by IS NULL
                """,
                book_id
            )
            
            # Get chapters awaiting approval  
            chapters = await conn.fetch(
                """
                SELECT 'chapter' as entity_type, chapter_id as entity_id, book_id, 
                       CONCAT('Chapter ', chapter_number, ': ', chapter_title) as name,
                       status, approved_by, approved_date, updated_at
                FROM book_chapters
                WHERE book_id = $1 AND status = 'done' AND approved_by IS NULL
                ORDER BY chapter_number
                """,
                book_id
            )
            
            # Combine all results
            all_entities = list(books) + list(outlines) + list(chapters)
            return [dict(row) for row in all_entities]
    
    async def add_research_item(self, research: ResearchItemCreate) -> int:
        """Add research item to database.
        
        Args:
            research: ResearchItemCreate model with validated data (book_id auto-converted to UUID)
            
        Returns:
            Integer research_id
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO research_items 
                (book_id, topic, content, source_url, research_type, chapter_reference, 
                 relevance_score, status, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                RETURNING research_id
                """,
                research.book_id,  # Already a UUID object!
                research.topic,
                research.content,
                research.source_url,
                research.research_type,
                research.chapter_reference,
                research.relevance_score,
                research.status.value if research.status else 'pending',  # Get enum value
                json.dumps(research.metadata) if research.metadata else '{}'
            )
            return row['research_id']
    
    async def get_research_items(self, book_id: UUID, category: str = None) -> List[Dict[str, Any]]:
        """Get research items for a project."""
        async with self.pool.acquire() as conn:
            if category:
                rows = await conn.fetch(
                    "SELECT * FROM research_items WHERE book_id = $1 AND research_type = $2",
                    book_id, category
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM research_items WHERE book_id = $1",
                    book_id
                )
            return [dict(row) for row in rows]
    
    async def get_research_count(self, book_id: UUID) -> int:
        """Get count of research items for a project."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) as count FROM research_items WHERE book_id = $1",
                book_id
            )
            return row['count'] if row else 0
    
    async def get_chapters_by_status(self, book_id: UUID, status: str) -> List[Dict[str, Any]]:
        """Get chapters with a specific status."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM book_chapters WHERE book_id = $1 AND status = $2 ORDER BY chapter_number",
                book_id, status
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def add_chapter(self, chapter: ChapterCreate) -> int:
        """Add a chapter to the database (idempotent - upserts if exists).
        
        Args:
            chapter: ChapterCreate model with validated data (book_id auto-converted to UUID)
            
        Returns:
            Integer chapter_id
        """
        async with self.pool.acquire() as conn:
            # Use INSERT ... ON CONFLICT for idempotency
            row = await conn.fetchrow(
                """
                INSERT INTO book_chapters 
                (book_id, chapter_number, chapter_title, content, status, word_count, written_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (book_id, chapter_number) 
                DO UPDATE SET 
                    chapter_title = EXCLUDED.chapter_title,
                    content = COALESCE(EXCLUDED.content, book_chapters.content),
                    status = CASE 
                        WHEN book_chapters.status IN ('published', 'approved') THEN book_chapters.status
                        ELSE EXCLUDED.status 
                    END,
                    word_count = EXCLUDED.word_count,
                    written_by = COALESCE(EXCLUDED.written_by, book_chapters.written_by),
                    updated_at = NOW()
                RETURNING chapter_id
                """,
                chapter.book_id,  # Already a UUID object!
                chapter.chapter_number,
                chapter.chapter_title,
                chapter.content,
                chapter.status.value,  # Get enum value
                chapter.word_count,
                chapter.written_by
            )
            return row['chapter_id']
    
    async def get_chapters(self, book_id: UUID) -> List[Dict[str, Any]]:
        """Get all chapters for a project."""
        async with self.pool.acquire() as conn:
            result = await conn.fetch(
                "SELECT * FROM book_chapters WHERE book_id = $1 ORDER BY chapter_number",
                book_id
            )
            return [dict(row) for row in result]
    
    async def update_chapter(self, chapter_id: int, content: str = None, status: str = None) -> bool:
        """Update chapter content or status and recalculate project completion."""
        book_id = None
        
        async with self.pool.acquire() as conn:
            # Get book_id for completion recalculation
            if status:  # Only need book_id if status is changing
                result = await conn.fetchrow(
                    "SELECT book_id FROM book_chapters WHERE chapter_id = $1",
                    chapter_id
                )
                book_id = result['book_id'] if result else None
            
            if content and status:
                await conn.execute(
                    "UPDATE book_chapters SET content = $1, status = $2, updated_at = NOW() WHERE chapter_id = $3",
                    content, status, chapter_id
                )
            elif content:
                await conn.execute(
                    "UPDATE book_chapters SET content = $1, updated_at = NOW() WHERE chapter_id = $2",
                    content, chapter_id
                )
            elif status:
                await conn.execute(
                    "UPDATE book_chapters SET status = $1, updated_at = NOW() WHERE chapter_id = $2",
                    status, chapter_id
                )
        
        # Update completion percentage if status changed
        if status and book_id:
            await self.update_project_completion(book_id)
            
        return True
    
    async def execute_in_transaction(self, operations: List[tuple]) -> List[Any]:
        """Execute multiple operations in a single transaction.
        
        Args:
            operations: List of (query, *args) tuples
            
        Returns:
            List of results from each operation
            
        Example:
            results = await db_operations.execute_in_transaction([
                ("INSERT INTO projects ... RETURNING book_id", title, genre),
                ("INSERT INTO chapters ... RETURNING chapter_id", book_id, 1, "Intro")
            ])
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                results = []
                for query, *args in operations:
                    if "RETURNING" in query.upper():
                        result = await conn.fetchrow(query, *args)
                        results.append(dict(result) if result else None)
                    else:
                        await conn.execute(query, *args)
                        results.append(None)
                return results
    
    # ==================== Cost Tracking Methods ====================
    
    @retry_on_db_error(max_retries=3)
    async def log_api_cost(self, cost_entry: CostEntryCreate) -> int:
        """Log AI API usage and cost for a project.
        
        Args:
            cost_entry: CostEntryCreate model with validated data (book_id auto-converted to UUID)
            
        Returns:
            Integer cost_id
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO ai_api_costs 
                (book_id, agent_name, operation_type, prompt_tokens, 
                 completion_tokens, total_tokens, estimated_cost_usd, model_name)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING cost_id
                """,
                cost_entry.book_id,  # Already a UUID object!
                cost_entry.agent_name,
                cost_entry.operation_type,
                0,  # prompt_tokens (will update CostEntryCreate model separately)
                0,  # completion_tokens
                cost_entry.tokens_used,  # total_tokens
                cost_entry.cost_usd,  # estimated_cost_usd
                cost_entry.model_name
            )
            logger.info(
                f"Logged API cost for {cost_entry.agent_name}/{cost_entry.operation_type}: "
                f"{cost_entry.tokens_used} tokens = ${cost_entry.cost_usd:.4f}"
            )
            return row['cost_id']
    
    @retry_on_db_error(max_retries=3)
    async def get_project_total_cost(self, book_id: UUID) -> float:
        """Get total estimated cost for a project.
        
        Args:
            book_id: UUID of the book project
            
        Returns:
            Total cost in USD
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(estimated_cost_usd), 0.0) as total_cost
                FROM ai_api_costs
                WHERE book_id = $1
                """,
                book_id
            )
            return float(row['total_cost']) if row else 0.0
    
    @retry_on_db_error(max_retries=3)
    async def get_project_cost_breakdown(self, book_id: UUID) -> List[Dict[str, Any]]:
        """Get detailed cost breakdown by agent for a project.
        
        Args:
            book_id: UUID of the book project
            
        Returns:
            List of cost breakdown records with agent, calls, tokens, cost
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    agent_name,
                    operation_type,
                    COUNT(*) as api_calls,
                    SUM(prompt_tokens) as total_prompt_tokens,
                    SUM(completion_tokens) as total_completion_tokens,
                    SUM(total_tokens) as total_tokens,
                    SUM(estimated_cost_usd) as total_cost
                FROM ai_api_costs
                WHERE book_id = $1
                GROUP BY agent_name, operation_type
                ORDER BY total_cost DESC
                """,
                book_id
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_all_project_costs(self) -> List[Dict[str, Any]]:
        """Get total cost summary for all projects.
        
        Returns:
            List of projects with their total costs
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    p.book_id,
                    p.title,
                    p.status,
                    COALESCE(SUM(c.estimated_cost_usd), 0.0) as total_cost,
                    COALESCE(SUM(c.total_tokens), 0) as total_tokens
                FROM books p
                LEFT JOIN ai_api_costs c ON p.book_id = c.book_id
                GROUP BY p.book_id, p.title, p.status
                ORDER BY total_cost DESC
                """,
            )
            return [dict(row) for row in rows]
    
    async def get_database_schema(self) -> Dict[str, Any]:
        """
        Get database schema information for all tables.
        Returns table names, columns, types, and relationships.
        """
        schema_query = """
            SELECT 
                t.table_name,
                c.column_name,
                c.data_type,
                c.is_nullable,
                c.column_default,
                tc.constraint_type,
                kcu.referenced_table_name,
                kcu.referenced_column_name
            FROM information_schema.tables t
            LEFT JOIN information_schema.columns c 
                ON t.table_name = c.table_name
            LEFT JOIN information_schema.key_column_usage kcu
                ON c.table_name = kcu.table_name 
                AND c.column_name = kcu.column_name
            LEFT JOIN information_schema.table_constraints tc
                ON kcu.constraint_name = tc.constraint_name
            WHERE t.table_schema = 'public'
                AND t.table_type = 'BASE TABLE'
            ORDER BY t.table_name, c.ordinal_position
        """
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(schema_query)
            
            # Organize by table
            schema = {}
            for row in rows:
                table = row['table_name']
                if table not in schema:
                    schema[table] = {
                        'columns': [],
                        'primary_keys': [],
                        'foreign_keys': []
                    }
                
                col_info = {
                    'name': row['column_name'],
                    'type': row['data_type'],
                    'nullable': row['is_nullable'] == 'YES',
                    'default': row['column_default']
                }
                schema[table]['columns'].append(col_info)
                
                if row['constraint_type'] == 'PRIMARY KEY':
                    schema[table]['primary_keys'].append(row['column_name'])
                elif row['constraint_type'] == 'FOREIGN KEY':
                    schema[table]['foreign_keys'].append({
                        'column': row['column_name'],
                        'references_table': row['referenced_table_name'],
                        'references_column': row['referenced_column_name']
                    })
            
            return schema
    
    async def query_projects_with_filters(
        self, 
        status_filter: Optional[str] = None,
        genre_filter: Optional[str] = None,
        order_by: str = "created_at DESC",
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Query projects with flexible filters.
        
        Args:
            status_filter: Filter by status (e.g., 'qa', 'editing', 'published')
            genre_filter: Filter by genre (partial match)
            order_by: ORDER BY clause (e.g., 'created_at DESC', 'title ASC')
            limit: Maximum number of results
        
        Returns:
            List of project dictionaries with chapter counts
        """
        query = """
            SELECT 
                p.book_id,
                p.title,
                p.status,
                p.genre,
                p.target_audience,
                p.created_at,
                p.updated_at,
                COUNT(c.chapter_id) as total_chapters,
                COUNT(CASE WHEN c.status = 'final' THEN 1 END) as completed_chapters,
                COUNT(CASE WHEN c.status IN ('draft', 'editing', 'qa') THEN 1 END) as in_progress_chapters
            FROM books p
            LEFT JOIN book_chapters c ON p.book_id = c.book_id
            WHERE 1=1
        """
        
        params = []
        param_count = 1
        
        if status_filter:
            query += f" AND p.status = ${param_count}"
            params.append(status_filter)
            param_count += 1
        
        if genre_filter:
            query += f" AND LOWER(p.genre) LIKE LOWER(${param_count})"
            params.append(f"%{genre_filter}%")
            param_count += 1
        
        query += " GROUP BY p.book_id, p.title, p.status, p.genre, p.target_audience, p.created_at, p.updated_at"
        
        # Validate and sanitize ORDER BY to prevent SQL injection
        # Allow ordering by computed columns (completed_chapters, total_chapters, in_progress_chapters)
        allowed_columns = [
            'created_at', 'updated_at', 'title', 'status', 'genre',
            'completed_chapters', 'total_chapters', 'in_progress_chapters'
        ]
        order_parts = order_by.split()
        if len(order_parts) >= 1:
            col = order_parts[0].lower()
            direction = order_parts[1].upper() if len(order_parts) > 1 else 'DESC'
            if col in allowed_columns and direction in ['ASC', 'DESC']:
                query += f" ORDER BY {col} {direction}"
            else:
                query += " ORDER BY created_at DESC"  # Safe default
        else:
            query += " ORDER BY created_at DESC"
        
        if limit:
            query += f" LIMIT {limit}"
        else:
            query += " LIMIT 200"  # Default max limit
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]
    
    async def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """
        Authenticate user with username and password.
        Returns user data if authentication succeeds, None otherwise.
        """
        import bcrypt
        
        async with self.pool.acquire() as conn:
            # Get user by username
            user = await conn.fetchrow(
                """
                SELECT user_id, username, password_hash, email, role, created_at
                FROM users
                WHERE username = $1 AND is_active = true
                """,
                username
            )
            
            if not user:
                return None
            
            # Verify password
            password_hash = user['password_hash']
            if bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8')):
                return {
                    'user_id': str(user['user_id']),
                    'username': user['username'],
                    'email': user['email'],
                    'role': user['role']
                }
            
            return None
    
    async def create_user(self, username: str, password: str, email: str = None, role: str = 'user') -> str:
        """
        Create a new user with hashed password.
        Returns user_id of created user.
        """
        import bcrypt
        
        # Hash password
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (username, password_hash, email, role, is_active)
                VALUES ($1, $2, $3, $4, true)
                RETURNING user_id
                """,
                username, password_hash, email, role
            )
            return str(row['user_id'])
    
    async def set_active_project(self, user_id: str, book_id: str) -> bool:
        """
        Set the active/current project for a user.
        This is the project context they're currently working on.
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE users 
                SET active_book_id = $1, updated_at = NOW()
                WHERE user_id = $2
                """,
                UUID(book_id), UUID(user_id)
            )
            return True
    
    async def get_active_project(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the user's currently active project.
        Returns project details or None if no active project.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT p.*
                FROM users u
                JOIN books p ON u.active_book_id = p.book_id
                WHERE u.user_id = $1
                """,
                UUID(user_id)
            )
            return dict(row) if row else None
    
    async def get_user_projects(self, user_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        """
        Get all projects for a specific user.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT p.*,
                    COUNT(c.chapter_id) as total_chapters,
                    COUNT(CASE WHEN c.status = 'final' THEN 1 END) as completed_chapters,
                    COUNT(CASE WHEN c.status IN ('draft', 'editing', 'qa') THEN 1 END) as in_progress_chapters
                FROM books p
                LEFT JOIN book_chapters c ON p.book_id = c.book_id
                WHERE p.user_id = $1
                GROUP BY p.book_id
                ORDER BY p.updated_at DESC
                LIMIT $2
                """,
                UUID(user_id), limit
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_comprehensive_project_summary(self, book_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Get a comprehensive aggregated summary of a book project.
        Includes project details, chapter statistics, research items, editing feedback,
        quality checks, AI costs, and overall progress metrics.
        
        Args:
            book_id: UUID of the book project
            
        Returns:
            Dictionary with comprehensive project summary including:
            - Project basic info (title, status, genre, etc.)
            - Chapter statistics (total, completed, in_progress, word counts)
            - Research statistics (total items, topics covered)
            - Editing feedback count and severity breakdown
            - Quality check results
            - AI API costs (total cost, tokens used)
            - Timeline information (created, last updated)
        """
        async with self.pool.acquire() as conn:
            # Main project query with aggregated statistics
            summary = await conn.fetchrow(
                """
                SELECT 
                    -- Project basics
                    p.book_id,
                    p.title,
                    p.description,
                    p.status,
                    p.pending_approval,
                    p.genre,
                    p.target_audience,
                    p.created_by,
                    p.created_at,
                    p.updated_at,
                    p.metadata,
                    
                    -- Chapter statistics
                    COUNT(DISTINCT c.chapter_id) as total_chapters,
                    COUNT(DISTINCT CASE WHEN c.status = 'final' THEN c.chapter_id END) as completed_chapters,
                    COUNT(DISTINCT CASE WHEN c.status = 'draft' THEN c.chapter_id END) as draft_chapters,
                    COUNT(DISTINCT CASE WHEN c.status = 'editing' THEN c.chapter_id END) as editing_chapters,
                    COUNT(DISTINCT CASE WHEN c.status = 'qa' THEN c.chapter_id END) as qa_chapters,
                    COALESCE(SUM(c.word_count), 0) as total_word_count,
                    
                    -- Outline information
                    (SELECT COUNT(*) FROM book_outlines WHERE book_id = p.book_id AND status != 'archived') as has_outline,
                    (SELECT status FROM book_outlines WHERE book_id = p.book_id AND status != 'archived' ORDER BY created_at DESC LIMIT 1) as outline_status,
                    (SELECT created_at FROM book_outlines WHERE book_id = p.book_id AND status != 'archived' ORDER BY created_at DESC LIMIT 1) as outline_created_at,
                    
                    -- Research statistics
                    COUNT(DISTINCT r.research_id) as total_research_items,
                    COUNT(DISTINCT r.topic) as unique_research_topics,
                    
                    -- Editing feedback statistics
                    COUNT(DISTINCT e.feedback_id) as total_feedback_items,
                    COUNT(DISTINCT CASE WHEN e.severity = 'critical' THEN e.feedback_id END) as critical_issues,
                    COUNT(DISTINCT CASE WHEN e.severity = 'major' THEN e.feedback_id END) as major_issues,
                    COUNT(DISTINCT CASE WHEN e.severity = 'minor' THEN e.feedback_id END) as minor_issues,
                    COUNT(DISTINCT CASE WHEN e.status = 'resolved' THEN e.feedback_id END) as resolved_issues,
                    
                    -- Quality check statistics
                    COUNT(DISTINCT q.check_id) as total_quality_checks,
                    COUNT(DISTINCT CASE WHEN q.check_result = 'passed' THEN q.check_id END) as passed_checks,
                    COUNT(DISTINCT CASE WHEN q.check_result = 'failed' THEN q.check_id END) as failed_checks,
                    
                    -- AI cost statistics
                    COALESCE(SUM(ai.estimated_cost_usd), 0.0) as total_ai_cost,
                    COALESCE(SUM(ai.total_tokens), 0) as total_tokens_used,
                    COUNT(DISTINCT ai.cost_id) as total_api_calls
                    
                FROM books p
                LEFT JOIN book_chapters c ON p.book_id = c.book_id
                LEFT JOIN research_items r ON p.book_id = r.book_id
                LEFT JOIN editing_feedback e ON c.chapter_id = e.chapter_id
                LEFT JOIN quality_checks q ON c.chapter_id = q.chapter_id
                LEFT JOIN ai_api_costs ai ON p.book_id = ai.book_id
                WHERE p.book_id = $1
                GROUP BY p.book_id, p.title, p.description, p.status, p.genre, 
                         p.target_audience, p.created_by, p.created_at, p.updated_at, p.metadata
                """,
                book_id
            )
            
            if not summary:
                return None
            
            result = dict(summary)
            
            # Calculate derived metrics
            total_chapters = result['total_chapters']
            completed_chapters = result['completed_chapters']
            
            if total_chapters > 0:
                result['completion_percentage'] = round((completed_chapters / total_chapters) * 100, 2)
            else:
                result['completion_percentage'] = 0.0
            
            # Calculate quality score (percentage of passed checks)
            total_checks = result['total_quality_checks']
            passed_checks = result['passed_checks']
            
            if total_checks > 0:
                result['quality_score'] = round((passed_checks / total_checks) * 100, 2)
            else:
                result['quality_score'] = None
            
            # Calculate issue resolution rate
            total_feedback = result['total_feedback_items']
            resolved_issues = result['resolved_issues']
            
            if total_feedback > 0:
                result['issue_resolution_rate'] = round((resolved_issues / total_feedback) * 100, 2)
            else:
                result['issue_resolution_rate'] = None
            
            # Format costs
            result['total_ai_cost'] = float(result['total_ai_cost'])
            
            # Add workflow guidance based on pending_approval status
            pending = result.get('pending_approval')
            has_outline = result.get('has_outline', 0) > 0
            outline_status = result.get('outline_status')
            
            if pending:
                result['is_blocked'] = True
                if pending == 'outline':
                    result['blocker_reason'] = "Waiting for outline approval"
                    result['next_action'] = "Review and approve/reject the outline to continue"
                    result['user_action_required'] = "Say 'show outline' to review or 'approve outline' to proceed"
                elif pending == 'chapter_review':
                    result['blocker_reason'] = "Waiting for chapter review"
                    result['next_action'] = "Review completed chapters and provide feedback"
                    result['user_action_required'] = "Say 'show chapters' to review"
                else:
                    result['blocker_reason'] = f"Waiting for approval: {pending}"
                    result['next_action'] = "User approval required"
                    result['user_action_required'] = "Contact support or check system status"
            else:
                result['is_blocked'] = False
                # Provide guidance on what's happening based on status and outline
                status = result.get('status', '')
                
                # Check outline status first
                if not has_outline:
                    result['outline_info'] = "No outline created yet"
                    if status == 'planning':
                        result['next_action'] = "Planning agent will create book outline"
                    else:
                        result['next_action'] = "Book has chapters but no formal outline - may be imported data"
                elif outline_status == 'draft':
                    result['outline_info'] = "Outline created, waiting for approval"
                    result['next_action'] = "Review outline and approve to continue workflow"
                    result['user_action_required'] = "Say 'show outline' to review or 'approve outline' to proceed"
                elif outline_status == 'approved':
                    result['outline_info'] = "Outline approved"
                else:
                    result['outline_info'] = f"Outline status: {outline_status}"
                
                # Then provide status-based guidance
                if status == 'todo':
                    if not has_outline:
                        result['next_action'] = "Planning agent will create book outline"
                    else:
                        result['next_action'] = "Ready to begin - waiting for workflow start"
                elif status == 'doing':
                    # Determine current phase based on progress
                    if not has_outline or outline_status == 'draft':
                        result['next_action'] = "Planning agent creating/refining book outline"
                    elif research_count == 0:
                        result['next_action'] = "Research agent gathering information for chapters"
                    elif total_chapters == 0:
                        result['next_action'] = "Writer agent drafting chapters"
                    else:
                        result['next_action'] = "Agents working on chapters (writing/editing/QA)"
                elif status == 'done':
                    result['next_action'] = "Book is complete!"
                elif status == 'paused':
                    result['next_action'] = "Workflow paused - resume to continue"
                elif status == 'cancelled':
                    result['next_action'] = "Book project cancelled"
                else:
                    result['next_action'] = "Workflow in progress"
            
            # CRITICAL: Detect stalled workflows (status says one thing, reality is another)
            status = result.get('status', '')
            has_outline = result.get('has_outline', 0)
            outline_status = result.get('outline_status')
            research_count = result.get('total_research_items', 0)
            total_chapters = result.get('total_chapters', 0)
            
            # Check for workflow stalls
            is_stalled = False
            stall_reason = None
            
            if status == 'doing':
                # Check for workflow stalls based on missing prerequisites
                import datetime
                updated_at = result.get('updated_at')
                time_since_update = None
                if updated_at:
                    time_since_update = datetime.datetime.now(datetime.timezone.utc) - updated_at
                
                # Doing but no outline = stalled
                if not has_outline:
                    if time_since_update and time_since_update.total_seconds() > 300:  # 5 minutes
                        is_stalled = True
                        stall_reason = f"Book is in 'doing' status but no outline was created after {int(time_since_update.total_seconds() / 60)} minutes. Planning workflow may be stalled."
                
                # Doing but outline not approved = stalled (if old)
                elif outline_status == 'draft' and time_since_update and time_since_update.total_seconds() > 1800:  # 30 minutes
                    is_stalled = True
                    stall_reason = "Book is in 'doing' status but outline has been pending approval for over 30 minutes. User action may be required."
                
                # Doing with approved outline but no research items and no recent activity
                elif outline_status == 'approved' and research_count == 0 and time_since_update and time_since_update.total_seconds() > 300:  # 5 minutes
                    is_stalled = True
                    stall_reason = f"Book is in 'doing' status with approved outline, but no research has been collected in {int(time_since_update.total_seconds() / 60)} minutes. Research workflow may not have been triggered."
                
                # Doing with research but no chapters and old timestamp
                elif research_count > 0 and total_chapters == 0 and time_since_update and time_since_update.total_seconds() > 600:  # 10 minutes
                    is_stalled = True
                    stall_reason = f"Book is in 'doing' status with research available, but no chapters have been created in {int(time_since_update.total_seconds() / 60)} minutes. Writing workflow may be stalled."
            
            elif status == 'todo':
                # Planning but has chapters = inconsistent (imported data)
                if total_chapters > 0 and not has_outline:
                    is_stalled = True
                    stall_reason = "Book has chapters but no outline and is still in planning phase. This may be imported data - consider creating an outline or changing status."
            
            result['is_stalled'] = is_stalled
            result['stall_reason'] = stall_reason
            
            # Generate workflow visualization
            result['workflow_visual'] = self._generate_workflow_visual(result)
            
            return result
    
    def _generate_workflow_visual(self, summary: Dict[str, Any]) -> str:
        """
        Generate ASCII visual representation of workflow progress.
        
        Args:
            summary: Project summary dictionary from get_comprehensive_project_summary
            
        Returns:
            Formatted ASCII workflow visualization string
        """
        status = summary.get('status', 'planning')
        pending = summary.get('pending_approval')
        outline_status = summary.get('outline_status')
        has_outline = summary.get('has_outline', 0)
        total_chapters = summary.get('total_chapters', 0)
        completed_chapters = summary.get('completed_chapters', 0)
        research_count = summary.get('total_research_items', 0)
        is_stalled = summary.get('is_stalled', False)
        stall_reason = summary.get('stall_reason')
        
        # Define workflow stages (simplified to match new system)
        stages = [
            ('todo', 'Planning'),
            ('doing', 'Active Work'),
            ('done', 'Complete'),
            ('paused', 'Paused'),
            ('cancelled', 'Cancelled')
        ]
        
        # Build visual
        visual = []
        visual.append("BOOK PRODUCTION PIPELINE")
        visual.append("=" * 60)
        visual.append("")
        
        for idx, (stage_key, stage_name) in enumerate(stages, 1):
            # Determine stage status
            if stage_key == status:
                # Current stage - check if stalled
                if is_stalled:
                    marker = "[!!!!]"
                    status_text = "STALLED"
                else:
                    marker = "[>>>>]"
                    status_text = "IN PROGRESS"
            elif self._is_stage_completed(stage_key, status, stages):
                marker = "[DONE]"
                status_text = "COMPLETED"
            else:
                marker = "[----]"
                status_text = "PENDING"
            
            # Build stage details
            details = self._get_stage_details(
                stage_key, summary, outline_status, has_outline,
                total_chapters, completed_chapters, research_count, pending
            )
            
            # Format line
            line = f"{idx}. {marker} {stage_name:<12} | {status_text:<12} | {details}"
            visual.append(line)
        
        visual.append("")
        visual.append("Legend: [DONE] = Completed  [>>>>] = Active  [!!!!] = STALLED  [----] = Pending")
        
        # Add stall warning if detected
        if is_stalled and stall_reason:
            visual.append("")
            visual.append("[WARNING] WORKFLOW STALLED")
            visual.append("=" * 60)
            visual.append(stall_reason)
            visual.append("")
            visual.append("ACTION REQUIRED: See 'Next Steps' above for how to resolve.")
        
        return "\n".join(visual)
    
    def _is_stage_completed(self, stage_key: str, current_status: str, stages: list) -> bool:
        """Check if a stage is completed based on current status."""
        stage_order = [s[0] for s in stages]
        try:
            stage_idx = stage_order.index(stage_key)
            current_idx = stage_order.index(current_status)
            return stage_idx < current_idx
        except ValueError:
            return False
    
    def _get_stage_details(self, stage_key: str, summary: Dict, outline_status: str,
                          has_outline: int, total_chapters: int, completed_chapters: int,
                          research_count: int, pending: str) -> str:
        """Get detailed information for a workflow stage."""
        if stage_key == 'todo':
            if has_outline:
                return f"Outline exists (status: {outline_status})"
            else:
                return "Ready to begin - outline will be created"
        
        elif stage_key == 'doing':
            # Show current progress within the doing phase
            if not has_outline:
                return "Creating outline..."
            elif outline_status == 'draft' and pending == 'outline':
                return "Outline pending approval"
            elif research_count == 0:
                return "Gathering research..."
            elif total_chapters == 0:
                return "Starting chapter writing..."
            else:
                return f"Active work: {completed_chapters}/{total_chapters} chapters done"
        
        elif stage_key == 'done':
            return f"Complete: {total_chapters} chapters published"
        
        elif stage_key == 'paused':
            return "Workflow temporarily paused"
        
        elif stage_key == 'cancelled':
            return "Project cancelled"
        
        return ""
    
    # ==================== CHAPTER-LEVEL DETAILS ====================
    
    @retry_on_db_error(max_retries=3)
    async def get_chapter_details(self, chapter_id: UUID) -> Optional[Dict[str, Any]]:
        """Get comprehensive details for a specific chapter including content, feedback, and quality checks."""
        async with self.pool.acquire() as conn:
            chapter = await conn.fetchrow(
                """
                SELECT c.*,
                    COUNT(DISTINCT e.feedback_id) as feedback_count,
                    COUNT(DISTINCT CASE WHEN e.status != 'resolved' THEN e.feedback_id END) as unresolved_feedback,
                    COUNT(DISTINCT q.check_id) as quality_checks,
                    COUNT(DISTINCT CASE WHEN q.check_result = 'passed' THEN q.check_id END) as passed_checks
                FROM book_chapters c
                LEFT JOIN editing_feedback e ON c.chapter_id = e.chapter_id
                LEFT JOIN quality_checks q ON c.chapter_id = q.chapter_id
                WHERE c.chapter_id = $1
                GROUP BY c.chapter_id
                """,
                chapter_id
            )
            
            if not chapter:
                return None
            
            result = dict(chapter)
            
            # Get feedback items
            feedback = await conn.fetch(
                """
                SELECT feedback_id, feedback_type, severity, feedback_content, 
                       status, resolved_at, created_at, created_by
                FROM editing_feedback
                WHERE chapter_id = $1
                ORDER BY severity DESC, created_at DESC
                """,
                chapter_id
            )
            result['feedback_items'] = [dict(f) for f in feedback]
            
            # Get quality checks
            checks = await conn.fetch(
                """
                SELECT check_id, check_type, passed, details, checked_at, checked_by
                FROM quality_checks
                WHERE chapter_id = $1
                ORDER BY checked_at DESC
                """,
                chapter_id
            )
            result['quality_check_items'] = [dict(c) for c in checks]
            
            return result
    
    @retry_on_db_error(max_retries=3)
    async def get_chapters_by_status(self, book_id: UUID, status: str) -> List[Dict[str, Any]]:
        """Get all chapters filtered by status."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT c.*,
                    COUNT(DISTINCT e.feedback_id) as feedback_count,
                    COUNT(DISTINCT CASE WHEN e.status = 'open' THEN e.feedback_id END) as unresolved_issues
                FROM book_chapters c
                LEFT JOIN editing_feedback e ON c.chapter_id = e.chapter_id
                WHERE c.book_id = $1 AND c.status = $2
                GROUP BY c.chapter_id
                ORDER BY c.chapter_number
                """,
                book_id, status
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_next_chapter_to_work_on(self, book_id: UUID) -> Optional[Dict[str, Any]]:
        """Smart recommendation of which chapter needs attention next based on status and issues."""
        async with self.pool.acquire() as conn:
            # Priority: chapters with unresolved critical issues > draft > editing > qa
            chapter = await conn.fetchrow(
                """
                SELECT c.*,
                    COUNT(DISTINCT e.feedback_id) FILTER (WHERE e.status = 'open' AND e.severity = 'critical') as critical_issues,
                    COUNT(DISTINCT e.feedback_id) FILTER (WHERE e.status = 'open') as total_unresolved,
                    CASE 
                        WHEN c.status = 'draft' THEN 1
                        WHEN c.status = 'editing' THEN 2
                        WHEN c.status = 'qa' THEN 3
                        ELSE 4
                    END as priority
                FROM book_chapters c
                LEFT JOIN editing_feedback e ON c.chapter_id = e.chapter_id
                WHERE c.book_id = $1 AND c.status != 'final'
                GROUP BY c.chapter_id
                ORDER BY 
                    critical_issues DESC,
                    priority ASC,
                    total_unresolved DESC,
                    c.chapter_number ASC
                LIMIT 1
                """,
                book_id
            )
            return dict(chapter) if chapter else None
    
    # ==================== RESEARCH INSIGHTS ====================
    
    @retry_on_db_error(max_retries=3)
    async def search_research_items(self, book_id: UUID, keyword: str) -> List[Dict[str, Any]]:
        """Search research content by topic or keyword using full-text search."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT r.*,
                    ts_rank(to_tsvector('english', r.topic || ' ' || r.content), 
                            plainto_tsquery('english', $2)) as relevance_score
                FROM research_items r
                WHERE r.book_id = $1
                AND (
                    to_tsvector('english', r.topic || ' ' || r.content) @@ plainto_tsquery('english', $2)
                    OR r.topic ILIKE $3
                    OR r.content ILIKE $3
                )
                ORDER BY relevance_score DESC, r.created_at DESC
                LIMIT 50
                """,
                book_id, keyword, f'%{keyword}%'
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_research_by_chapter(self, book_id: UUID, chapter_number: int) -> List[Dict[str, Any]]:
        """Get research items relevant to a specific chapter."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT r.*
                FROM research_items r
                WHERE r.book_id = $1
                AND (
                    r.metadata->>'chapter_number' = $2::text
                    OR r.metadata->>'related_chapters' @> $2::text
                )
                ORDER BY r.created_at DESC
                """,
                book_id, str(chapter_number)
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_unused_research(self, book_id: UUID) -> List[Dict[str, Any]]:
        """Find research items not yet incorporated into chapters."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT r.*
                FROM research_items r
                WHERE r.book_id = $1
                AND (
                    r.metadata->>'used_in_chapters' IS NULL
                    OR r.metadata->>'used_in_chapters' = '[]'
                )
                ORDER BY r.created_at DESC
                """,
                book_id
            )
            return [dict(row) for row in rows]
    
    # ==================== QUALITY & FEEDBACK TRACKING ====================
    
    @retry_on_db_error(max_retries=3)
    async def get_unresolved_issues(self, book_id: UUID, severity_filter: str = None) -> List[Dict[str, Any]]:
        """Get open editing feedback filtered by severity."""
        async with self.pool.acquire() as conn:
            query = """
                SELECT e.*, c.chapter_number, c.title as chapter_title
                FROM editing_feedback e
                JOIN book_chapters c ON e.chapter_id = c.chapter_id
                WHERE c.book_id = $1 AND e.status = 'open'
            """
            params = [book_id]
            
            if severity_filter:
                query += " AND e.severity = $2"
                params.append(severity_filter)
            
            query += " ORDER BY e.severity DESC, e.created_at DESC"
            
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_chapter_quality_report(self, chapter_id: UUID) -> Dict[str, Any]:
        """Detailed quality metrics for a specific chapter."""
        async with self.pool.acquire() as conn:
            report = await conn.fetchrow(
                """
                SELECT 
                    c.chapter_id,
                    c.chapter_number,
                    c.title,
                    c.status,
                    c.word_count,
                    COUNT(DISTINCT q.check_id) as total_checks,
                    COUNT(DISTINCT CASE WHEN q.passed = true THEN q.check_id END) as passed_checks,
                    COUNT(DISTINCT e.feedback_id) as total_feedback,
                    COUNT(DISTINCT CASE WHEN e.severity = 'critical' THEN e.feedback_id END) as critical_issues,
                    COUNT(DISTINCT CASE WHEN e.severity = 'major' THEN e.feedback_id END) as major_issues,
                    COUNT(DISTINCT CASE WHEN e.severity = 'minor' THEN e.feedback_id END) as minor_issues,
                    COUNT(DISTINCT CASE WHEN e.status != 'open' THEN e.feedback_id END) as resolved_issues
                FROM book_chapters c
                LEFT JOIN quality_checks q ON c.chapter_id = q.chapter_id
                LEFT JOIN editing_feedback e ON c.chapter_id = e.chapter_id
                WHERE c.chapter_id = $1
                GROUP BY c.chapter_id
                """,
                chapter_id
            )
            
            if not report:
                return None
            
            result = dict(report)
            
            # Calculate quality score
            if result['total_checks'] > 0:
                result['quality_score'] = round((result['passed_checks'] / result['total_checks']) * 100, 2)
            else:
                result['quality_score'] = None
            
            # Calculate issue resolution rate
            if result['total_feedback'] > 0:
                result['resolution_rate'] = round((result['resolved_issues'] / result['total_feedback']) * 100, 2)
            else:
                result['resolution_rate'] = None
            
            return result
    
    @retry_on_db_error(max_retries=3)
    async def get_project_quality_trends(self, book_id: UUID) -> List[Dict[str, Any]]:
        """Quality scores over time to track improvement."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    DATE(q.checked_at) as check_date,
                    COUNT(DISTINCT q.check_id) as checks_performed,
                    COUNT(DISTINCT CASE WHEN q.passed = true THEN q.check_id END) as checks_passed,
                    ROUND(AVG(CASE WHEN q.passed = true THEN 100.0 ELSE 0.0 END), 2) as daily_quality_score
                FROM quality_checks q
                JOIN book_chapters c ON q.chapter_id = c.chapter_id
                WHERE c.book_id = $1
                GROUP BY DATE(q.checked_at)
                ORDER BY check_date DESC
                LIMIT 30
                """,
                book_id
            )
            return [dict(row) for row in rows]
    
    # ==================== PROGRESS & TIMELINE ====================
    
    @retry_on_db_error(max_retries=3)
    async def get_project_timeline(self, book_id: UUID) -> List[Dict[str, Any]]:
        """Chronological view of all activities and updates."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    timestamp,
                    action,
                    entity_type,
                    entity_id,
                    details,
                    performed_by
                FROM audit_logs
                WHERE book_id = $1
                ORDER BY timestamp DESC
                LIMIT 100
                """,
                book_id
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_recent_activity(self, book_id: UUID, days: int = 7) -> List[Dict[str, Any]]:
        """What's been updated in last N days."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    timestamp,
                    action,
                    agent_name,
                    details
                FROM audit_logs
                WHERE book_id = $1
                AND timestamp >= NOW() - INTERVAL '%s days'
                ORDER BY timestamp DESC
                """,
                book_id, days
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_writing_velocity(self, book_id: UUID) -> Dict[str, Any]:
        """Words written per day/week and completion estimates."""
        async with self.pool.acquire() as conn:
            velocity = await conn.fetchrow(
                """
                WITH daily_words AS (
                    SELECT 
                        DATE(a.timestamp) as activity_date,
                        SUM(CASE 
                            WHEN a.action = 'chapter_created' OR a.action = 'chapter_updated'
                            THEN (a.details->>'word_count')::int
                            ELSE 0
                        END) as words_written
                    FROM audit_logs a
                    WHERE a.book_id = $1
                    AND a.timestamp >= NOW() - INTERVAL '30 days'
                    GROUP BY DATE(a.timestamp)
                ),
                project_stats AS (
                    SELECT 
                        COALESCE(SUM(c.word_count), 0) as current_word_count,
                        COUNT(CASE WHEN c.status = 'final' THEN 1 END) as completed_chapters,
                        COUNT(c.chapter_id) as total_chapters
                    FROM book_chapters c
                    WHERE c.book_id = $1
                )
                SELECT 
                    ROUND(AVG(dw.words_written), 2) as avg_words_per_day,
                    ROUND(AVG(dw.words_written) * 7, 2) as avg_words_per_week,
                    ps.current_word_count,
                    ps.completed_chapters,
                    ps.total_chapters,
                    CASE 
                        WHEN AVG(dw.words_written) > 0 
                        THEN ROUND((ps.total_chapters - ps.completed_chapters) * 
                             (ps.current_word_count::float / NULLIF(ps.completed_chapters, 0)) / 
                             AVG(dw.words_written))
                        ELSE NULL
                    END as estimated_days_to_completion
                FROM daily_words dw, project_stats ps
                GROUP BY ps.current_word_count, ps.completed_chapters, ps.total_chapters
                """,
                book_id
            )
            return dict(velocity) if velocity else {}
    
    # ==================== COST & RESOURCE MANAGEMENT ====================
    
    @retry_on_db_error(max_retries=3)
    async def get_cost_by_agent(self, book_id: UUID) -> List[Dict[str, Any]]:
        """Breakdown of AI costs by agent type."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    agent_name,
                    COUNT(*) as api_calls,
                    SUM(total_tokens) as total_tokens,
                    SUM(prompt_tokens) as prompt_tokens,
                    SUM(completion_tokens) as completion_tokens,
                    ROUND(SUM(estimated_cost_usd)::numeric, 4) as total_cost
                FROM ai_api_costs
                WHERE book_id = $1
                GROUP BY agent_name
                ORDER BY total_cost DESC
                """,
                book_id
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_cost_by_chapter(self, book_id: UUID) -> List[Dict[str, Any]]:
        """Which chapters cost the most to produce."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    c.chapter_id,
                    c.chapter_number,
                    c.title,
                    c.status,
                    COUNT(ai.cost_id) as api_calls,
                    COALESCE(SUM(ai.total_tokens), 0) as total_tokens,
                    ROUND(COALESCE(SUM(ai.estimated_cost_usd), 0.0)::numeric, 4) as total_cost
                FROM book_chapters c
                LEFT JOIN ai_api_costs ai ON c.chapter_id = ai.chapter_id
                WHERE c.book_id = $1
                GROUP BY c.chapter_id
                ORDER BY total_cost DESC
                """,
                book_id
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_cost_forecast(self, book_id: UUID) -> Dict[str, Any]:
        """Estimate total cost based on current progress."""
        async with self.pool.acquire() as conn:
            forecast = await conn.fetchrow(
                """
                WITH chapter_stats AS (
                    SELECT 
                        COUNT(*) FILTER (WHERE status = 'final') as completed_chapters,
                        COUNT(*) as total_chapters,
                        COALESCE(SUM(ai.estimated_cost_usd) FILTER (WHERE c.status = 'final'), 0.0) as completed_cost
                    FROM book_chapters c
                    LEFT JOIN ai_api_costs ai ON c.chapter_id = ai.chapter_id
                    WHERE c.book_id = $1
                ),
                total_cost AS (
                    SELECT COALESCE(SUM(estimated_cost_usd), 0.0) as spent_to_date
                    FROM ai_api_costs
                    WHERE book_id = $1
                )
                SELECT 
                    cs.completed_chapters,
                    cs.total_chapters,
                    ROUND(tc.spent_to_date::numeric, 4) as spent_to_date,
                    ROUND(cs.completed_cost::numeric, 4) as cost_for_completed,
                    CASE 
                        WHEN cs.completed_chapters > 0 
                        THEN ROUND((cs.completed_cost / cs.completed_chapters * cs.total_chapters)::numeric, 4)
                        ELSE NULL
                    END as estimated_total_cost,
                    CASE 
                        WHEN cs.completed_chapters > 0 
                        THEN ROUND((cs.completed_cost / cs.completed_chapters * 
                             (cs.total_chapters - cs.completed_chapters))::numeric, 4)
                        ELSE NULL
                    END as estimated_remaining_cost
                FROM chapter_stats cs, total_cost tc
                """,
                book_id
            )
            return dict(forecast) if forecast else {}
    
    # ==================== CONTENT ANALYTICS ====================
    
    @retry_on_db_error(max_retries=3)
    async def get_word_count_by_chapter(self, book_id: UUID) -> List[Dict[str, Any]]:
        """Chapter length distribution."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    chapter_id,
                    chapter_number,
                    title,
                    status,
                    word_count,
                    ROUND(AVG(word_count) OVER (), 0) as avg_chapter_length
                FROM book_chapters
                WHERE book_id = $1
                ORDER BY chapter_number
                """,
                book_id
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_outline_structure(self, book_id: UUID) -> Optional[Dict[str, Any]]:
        """Get the most recent book outline with full structure.
        Returns the outline content and parsed structure JSON."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 
                    outline_id,
                    book_id,
                    outline_content,
                    structure,
                    status,
                    created_at,
                    updated_at,
                    created_by
                FROM book_outlines
                WHERE book_id = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                book_id
            )
            if row:
                result = dict(row)
                # Parse structure JSON if it exists
                if result.get('structure'):
                    result['structure'] = json.loads(result['structure']) if isinstance(result['structure'], str) else result['structure']
                return result
            return None
    
    @retry_on_db_error(max_retries=3)
    async def get_outline(self, book_id: UUID) -> Optional[Dict[str, Any]]:
        """Alias for get_outline_structure - retrieves the active outline.
        Kept for compatibility with agent code that calls get_outline()."""
        return await self.get_outline_structure(book_id)
    
    @retry_on_db_error(max_retries=3)
    async def create_outline(self, outline: OutlineCreate) -> int:
        """Create a new book outline.
        
        Args:
            outline: OutlineCreate model with validated data (book_id auto-converted to UUID)
            
        Returns:
            Integer outline_id
            
        Note: Automatically archives previous outlines for same project to maintain
        one active outline per project.
        """
        async with self.pool.acquire() as conn:
            # Archive any existing outlines for this project
            await conn.execute(
                """
                UPDATE book_outlines 
                SET status = 'archived', updated_at = CURRENT_TIMESTAMP
                WHERE book_id = $1 AND status != 'archived'
                """,
                outline.book_id  # Already a UUID object!
            )
            
            # Create new outline
            outline_id = await conn.fetchval(
                """
                INSERT INTO book_outlines 
                (book_id, outline_content, structure, status, created_by)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING outline_id
                """,
                outline.book_id,  # Already a UUID object!
                outline.outline_content,
                json.dumps(outline.structure) if outline.structure else None,
                outline.status,
                outline.created_by
            )
            
            logger.info(f"Created outline {outline_id} for project {outline.book_id}")
            return outline_id
    
    @retry_on_db_error(max_retries=3)
    async def approve_outline(
        self,
        book_id: UUID,
        approved_by: str = "user",
        approved: bool = True
    ) -> bool:
        """Approve or reject the active outline.
        
        Args:
            book_id: UUID of the project
            approved_by: Name of approver (user, agent, etc.)
            approved: True to approve, False to reject
            
        Returns:
            True if successful
            
        Updates:
            - Outline status: draft -> approved (or archived if rejected)
            - Project status: outline_pending -> researching (or planning if rejected)
        """
        async with self.pool.acquire() as conn:
            # Update outline status
            # If rejected, archive the outline to allow new one to be created
            new_outline_status = 'approved' if approved else 'archived'
            await conn.execute(
                """
                UPDATE book_outlines 
                SET status = $1, updated_at = CURRENT_TIMESTAMP
                WHERE book_id = $2 AND status = 'draft'
                """,
                new_outline_status,
                book_id
            )
            
            # Update project status
            if approved:
                # When approved, move to active work phase
                new_project_status = 'doing'
            else:
                new_project_status = 'todo'  # Back to todo if rejected
            
            await conn.execute(
                """
                UPDATE books 
                SET status = $1, updated_at = CURRENT_TIMESTAMP
                WHERE book_id = $2
                """,
                new_project_status,
                book_id
            )
            
        # Update completion percentage after status change
        await self.update_project_completion(book_id)
        
        action = "approved" if approved else "rejected"
        logger.info(f"Outline {action} for project {book_id} by {approved_by}")
        return True
    
    @retry_on_db_error(max_retries=3)
    async def get_writing_style_metrics(self, book_id: UUID) -> Dict[str, Any]:
        """Reading level and consistency metrics across chapters."""
        async with self.pool.acquire() as conn:
            metrics = await conn.fetchrow(
                """
                SELECT 
                    COUNT(c.chapter_id) as total_chapters,
                    COALESCE(SUM(c.word_count), 0) as total_words,
                    ROUND(AVG(c.word_count), 0) as avg_chapter_words,
                    ROUND(STDDEV(c.word_count), 0) as word_count_stddev,
                    MIN(c.word_count) as min_chapter_words,
                    MAX(c.word_count) as max_chapter_words
                FROM book_chapters c
                WHERE c.book_id = $1 AND c.status != 'draft'
                """,
                book_id
            )
            return dict(metrics) if metrics else {}
    
    # ==================== COLLABORATION & HISTORY ====================
    
    @retry_on_db_error(max_retries=3)
    async def get_chapter_revision_history(self, chapter_id: UUID) -> List[Dict[str, Any]]:
        """Track all changes to a chapter."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    timestamp,
                    action,
                    details,
                    agent_name
                FROM audit_logs
                WHERE book_id = (SELECT book_id FROM book_chapters WHERE chapter_id = $1)
                AND action LIKE '%chapter%'
                ORDER BY timestamp DESC
                """,
                chapter_id
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_project_contributors(self, book_id: UUID) -> List[Dict[str, Any]]:
        """Who worked on what (agents and humans)."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    agent_name as contributor,
                    COUNT(*) as total_actions,
                    MIN(timestamp) as first_contribution,
                    MAX(timestamp) as last_contribution
                FROM audit_logs
                WHERE book_id = $1
                GROUP BY agent_name
                ORDER BY total_actions DESC
                """,
                book_id
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_audit_trail(self, book_id: UUID, action_type: str = None) -> List[Dict[str, Any]]:
        """Detailed audit log with optional filtering."""
        async with self.pool.acquire() as conn:
            query = """
                SELECT 
                    log_id,
                    timestamp,
                    action,
                    agent_name,
                    details
                FROM audit_logs
                WHERE book_id = $1
            """
            params = [book_id]
            
            if action_type:
                query += " AND action = $2"
                params.append(action_type)
            
            query += " ORDER BY timestamp DESC LIMIT 200"
            
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]
    
    # ==================== SMART SEARCH & DISCOVERY ====================
    
    @retry_on_db_error(max_retries=3)
    async def full_text_search_chapters(self, book_id: UUID, query: str) -> List[Dict[str, Any]]:
        """Search chapter content using full-text search."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    c.chapter_id,
                    c.chapter_number,
                    c.title,
                    c.status,
                    c.word_count,
                    ts_rank(to_tsvector('english', c.title || ' ' || COALESCE(c.content, '')), 
                            plainto_tsquery('english', $2)) as relevance
                FROM book_chapters c
                WHERE c.book_id = $1
                AND to_tsvector('english', c.title || ' ' || COALESCE(c.content, '')) 
                    @@ plainto_tsquery('english', $2)
                ORDER BY relevance DESC, c.chapter_number
                LIMIT 20
                """,
                book_id, query
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def find_similar_projects(self, book_id: UUID) -> List[Dict[str, Any]]:
        """Find other books by genre/topic similarity."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH current_project AS (
                    SELECT genre, target_audience, description
                    FROM books
                    WHERE book_id = $1
                )
                SELECT 
                    p.book_id,
                    p.title,
                    p.genre,
                    p.target_audience,
                    p.status,
                    CASE 
                        WHEN p.genre = cp.genre THEN 2
                        ELSE 0
                    END +
                    CASE 
                        WHEN p.target_audience = cp.target_audience THEN 1
                        ELSE 0
                    END as similarity_score
                FROM books p, current_project cp
                WHERE p.book_id != $1
                AND (p.genre = cp.genre OR p.target_audience = cp.target_audience)
                ORDER BY similarity_score DESC
                LIMIT 10
                """,
                book_id
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_cross_project_research(self, user_id: str, topic: str) -> List[Dict[str, Any]]:
        """Reusable research across user's books."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    r.research_id,
                    r.book_id,
                    p.title as project_title,
                    r.topic,
                    r.content,
                    r.source_url,
                    r.research_type,
                    r.created_at,
                    ts_rank(to_tsvector('english', r.topic || ' ' || r.content), 
                            plainto_tsquery('english', $2)) as relevance
                FROM research_items r
                JOIN books p ON r.book_id = p.book_id
                WHERE p.user_id = $1
                AND to_tsvector('english', r.topic || ' ' || r.content) 
                    @@ plainto_tsquery('english', $2)
                ORDER BY relevance DESC, r.created_at DESC
                LIMIT 30
                """,
                UUID(user_id), topic
            )
            return [dict(row) for row in rows]
    
    # ==================== APPROVAL WORKFLOW OPERATIONS ====================
    
    @retry_on_db_error()
    async def create_approval_request(
        self, 
        book_id: str, 
        milestone: str, 
        approval_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create new approval request for a project milestone.
        
        Args:
            book_id: UUID of the book project
            milestone: Type of approval ('outline', 'drafts', 'final')
            approval_data: JSONB data containing what's being approved
            
        Returns:
            Dict with approval_id and request details
        """
        if not self.pool:
            await self.initialize()
        
        async with self.pool.acquire() as conn:
            # Set project pending_approval status
            await conn.execute(
                """
                UPDATE books 
                SET pending_approval = $1, updated_at = CURRENT_TIMESTAMP
                WHERE book_id = $2
                """,
                milestone, UUID(book_id)
            )
            
            # Create approval request
            row = await conn.fetchrow(
                """
                INSERT INTO approval_requests (
                    book_id, milestone, status, approval_data
                ) VALUES ($1, $2, 'pending', $3)
                RETURNING approval_id, book_id, milestone, status, 
                          requested_at, approval_data
                """,
                UUID(book_id), milestone, json.dumps(approval_data)
            )
            
            logger.info(f"{self.agent_name}: Created approval request {row['approval_id']} for {milestone} on project {book_id}")
            return dict(row)
    
    @retry_on_db_error()
    async def get_pending_approvals(
        self, 
        user_id: Optional[str] = None, 
        book_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get pending approval requests.
        
        Args:
            user_id: Optional filter by user (checks project ownership)
            book_id: Optional filter by specific project
            
        Returns:
            List of pending approval requests with project details
        """
        if not self.pool:
            await self.initialize()
        
        async with self.pool.acquire() as conn:
            # Build dynamic query based on filters
            query = """
                SELECT 
                    a.approval_id, a.book_id, a.milestone, a.status,
                    a.requested_at, a.approval_data,
                    p.title as project_title, p.genre, p.target_audience
                FROM approval_requests a
                JOIN books p ON a.book_id = p.book_id
                WHERE a.status = 'pending'
            """
            params = []
            param_count = 1
            
            if user_id:
                query += f" AND p.user_id = ${param_count}"
                params.append(user_id)
                param_count += 1
            
            if book_id:
                query += f" AND a.book_id = ${param_count}"
                params.append(UUID(book_id))
                param_count += 1
            
            query += " ORDER BY a.requested_at ASC"
            
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]
    
    @retry_on_db_error()
    async def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """
        Get single approval request by ID.
        
        Args:
            approval_id: UUID of the approval request
            
        Returns:
            Approval request details or None if not found
        """
        if not self.pool:
            await self.initialize()
        
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 
                    a.approval_id, a.book_id, a.milestone, a.status,
                    a.requested_at, a.reviewed_at, a.reviewer_user_id,
                    a.decision, a.feedback, a.approval_data,
                    p.title as project_title
                FROM approval_requests a
                JOIN books p ON a.book_id = p.book_id
                WHERE a.approval_id = $1
                """,
                UUID(approval_id)
            )
            return dict(row) if row else None
    
    @retry_on_db_error()
    async def approve_milestone(
        self, 
        approval_id: str, 
        reviewer_user_id: str, 
        feedback: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Approve a milestone (mark approval request as approved).
        
        Args:
            approval_id: UUID of the approval request
            reviewer_user_id: ID of user who approved
            feedback: Optional approval feedback/comments
            
        Returns:
            Updated approval request details
        """
        if not self.pool:
            await self.initialize()
        
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Update approval request
                row = await conn.fetchrow(
                    """
                    UPDATE approval_requests
                    SET status = 'approved',
                        decision = 'approve',
                        reviewer_user_id = $1,
                        feedback = $2,
                        reviewed_at = CURRENT_TIMESTAMP
                    WHERE approval_id = $3
                    RETURNING approval_id, book_id, milestone, status, decision, feedback
                    """,
                    reviewer_user_id, feedback, UUID(approval_id)
                )
                
                if row:
                    # Clear pending_approval status and update last_approval_at
                    await conn.execute(
                        """
                        UPDATE books
                        SET pending_approval = NULL,
                            last_approval_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE book_id = $1
                        """,
                        row['book_id']
                    )
                    
                    logger.info(f"{self.agent_name}: Approved {row['milestone']} for project {row['book_id']}")
                    return dict(row)
                else:
                    raise ValueError(f"Approval request {approval_id} not found")
    
    @retry_on_db_error()
    async def reject_milestone(
        self, 
        approval_id: str, 
        reviewer_user_id: str, 
        feedback: str
    ) -> Dict[str, Any]:
        """
        Reject a milestone (mark approval request as rejected).
        
        Args:
            approval_id: UUID of the approval request
            reviewer_user_id: ID of user who rejected
            feedback: Required rejection feedback explaining why
            
        Returns:
            Updated approval request details
        """
        if not self.pool:
            await self.initialize()
        
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Update approval request
                row = await conn.fetchrow(
                    """
                    UPDATE approval_requests
                    SET status = 'rejected',
                        decision = 'reject',
                        reviewer_user_id = $1,
                        feedback = $2,
                        reviewed_at = CURRENT_TIMESTAMP
                    WHERE approval_id = $3
                    RETURNING approval_id, book_id, milestone, status, decision, feedback
                    """,
                    reviewer_user_id, feedback, UUID(approval_id)
                )
                
                if row:
                    # Keep pending_approval status (revision needed)
                    await conn.execute(
                        """
                        UPDATE books
                        SET updated_at = CURRENT_TIMESTAMP
                        WHERE book_id = $1
                        """,
                        row['book_id']
                    )
                    
                    logger.info(f"{self.agent_name}: Rejected {row['milestone']} for project {row['book_id']} - {feedback}")
                    return dict(row)
                else:
                    raise ValueError(f"Approval request {approval_id} not found")
    
    @retry_on_db_error()
    async def get_approval_history(self, book_id: str) -> List[Dict[str, Any]]:
        """
        Get complete approval history for a project (audit trail).
        
        Args:
            book_id: UUID of the book project
            
        Returns:
            List of all approval requests (pending, approved, rejected) in chronological order
        """
        if not self.pool:
            await self.initialize()
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    approval_id, book_id, milestone, status, decision,
                    requested_at, reviewed_at, reviewer_user_id, feedback
                FROM approval_requests
                WHERE book_id = $1
                ORDER BY requested_at ASC
                """,
                UUID(book_id)
            )
            return [dict(row) for row in rows]
    
    # ==================== MESSAGE TRACKING ====================
    
    @retry_on_db_error(max_retries=3)
    async def save_agent_message(
        self,
        book_id: UUID,
        event_type: str,
        publisher: str,
        payload: Dict[str, Any],
        routing_key: str,
        exchange: str = "book-workflow",
        delivery_status: str = "published",
        correlation_id: UUID = None
    ) -> UUID:
        """
        Save agent-to-agent message to database for complete audit trail.
        
        Args:
            book_id: UUID of the book project
            event_type: Event type (e.g., "planning.outline.created")
            publisher: Agent name that published the message
            payload: Event payload (JSONB)
            routing_key: RabbitMQ routing key used
            exchange: RabbitMQ exchange name
            delivery_status: published, delivered, acknowledged, failed
            correlation_id: Optional correlation ID for tracking
            
        Returns:
            message_id: UUID of stored message
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO agent_messages 
                (book_id, event_type, publisher, payload, routing_key, exchange, 
                 delivery_status, correlation_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING message_id
                """,
                book_id, event_type, publisher, json.dumps(payload), 
                routing_key, exchange, delivery_status, correlation_id
            )
            return row['message_id']
    
    @retry_on_db_error(max_retries=3)
    async def update_message_status(
        self,
        message_id: UUID,
        delivery_status: str,
        subscriber: str = None,
        error_message: str = None
    ) -> bool:
        """
        Update message delivery status.
        
        Args:
            message_id: UUID of message
            delivery_status: published, delivered, acknowledged, failed
            subscriber: Agent that received/processed it
            error_message: Error details if failed
            
        Returns:
            True if updated successfully
        """
        async with self.pool.acquire() as conn:
            timestamp_field = None
            if delivery_status == "delivered":
                timestamp_field = "delivered_at"
            elif delivery_status == "acknowledged":
                timestamp_field = "acknowledged_at"
            
            query = """
                UPDATE agent_messages
                SET delivery_status = $2,
                    subscriber = COALESCE($3, subscriber),
                    error_message = COALESCE($4, error_message)
            """
            
            if timestamp_field:
                query += f", {timestamp_field} = CURRENT_TIMESTAMP"
            
            query += " WHERE message_id = $1"
            
            result = await conn.execute(query, message_id, delivery_status, subscriber, error_message)
            return result != "UPDATE 0"
    
    @retry_on_db_error(max_retries=3)
    async def get_agent_messages(
        self,
        book_id: UUID,
        event_type: str = None,
        publisher: str = None,
        delivery_status: str = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get agent messages with optional filtering.
        
        Args:
            book_id: UUID of the book project
            event_type: Optional filter by event type
            publisher: Optional filter by publisher
            delivery_status: Optional filter by status
            limit: Max messages to return
            
        Returns:
            List of messages ordered by published_at DESC
        """
        async with self.pool.acquire() as conn:
            query = """
                SELECT 
                    message_id, book_id, event_type, publisher, payload,
                    delivery_status, error_message, subscriber,
                    exchange, routing_key,
                    published_at, delivered_at, acknowledged_at,
                    correlation_id, reply_to
                FROM agent_messages
                WHERE book_id = $1
            """
            params = [book_id]
            param_num = 2
            
            if event_type:
                query += f" AND event_type = ${param_num}"
                params.append(event_type)
                param_num += 1
            
            if publisher:
                query += f" AND publisher = ${param_num}"
                params.append(publisher)
                param_num += 1
            
            if delivery_status:
                query += f" AND delivery_status = ${param_num}"
                params.append(delivery_status)
                param_num += 1
            
            query += f" ORDER BY published_at DESC LIMIT ${param_num}"
            params.append(limit)
            
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_message_flow(self, book_id: UUID, hours: int = 24) -> List[Dict[str, Any]]:
        """
        Get complete message flow for a project (last N hours).
        Shows event sequence and delivery status.
        
        Args:
            book_id: UUID of the book project
            hours: Look back this many hours
            
        Returns:
            List of messages with timing and status
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    message_id,
                    event_type,
                    publisher,
                    subscriber,
                    delivery_status,
                    published_at,
                    delivered_at,
                    acknowledged_at,
                    error_message,
                    EXTRACT(EPOCH FROM (COALESCE(acknowledged_at, delivered_at, CURRENT_TIMESTAMP) - published_at)) as latency_seconds
                FROM agent_messages
                WHERE book_id = $1
                AND published_at > CURRENT_TIMESTAMP - INTERVAL '%s hours'
                ORDER BY published_at ASC
                """ % hours,
                book_id
            )
            return [dict(row) for row in rows]
    
    @retry_on_db_error(max_retries=3)
    async def get_failed_messages(self, book_id: UUID = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get all failed messages for debugging.
        
        Args:
            book_id: Optional filter by project
            limit: Max messages to return
            
        Returns:
            List of failed messages
        """
        async with self.pool.acquire() as conn:
            if book_id:
                rows = await conn.fetch(
                    """
                    SELECT 
                        message_id, book_id, event_type, publisher,
                        error_message, published_at, payload
                    FROM agent_messages
                    WHERE book_id = $1 AND delivery_status = 'failed'
                    ORDER BY published_at DESC
                    LIMIT $2
                    """,
                    book_id, limit
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT 
                        message_id, book_id, event_type, publisher,
                        error_message, published_at, payload
                    FROM agent_messages
                    WHERE delivery_status = 'failed'
                    ORDER BY published_at DESC
                    LIMIT $1
                    """,
                    limit
                )
            return [dict(row) for row in rows]

    async def close(self):
        """Close the shared database connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info(f"{self.agent_name}: Shared database connection pool closed")
            self._pool = None


# Singleton instance
db_operations = DatabaseOperations()
