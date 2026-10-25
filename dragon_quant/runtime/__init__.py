"""Runtime contracts shared by the composition root.

``providers`` owns the single ``ProviderBundle`` definition; ``clock`` defines
the minimal passive clock contract.  Runtime modules are leaves: they must not
import feature modules or the composition root.
"""
